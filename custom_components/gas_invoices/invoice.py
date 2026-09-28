"""Парсване на PDF фактури от Костинбродгаз (КОСТИНБРОДГАЗ ООД) и разпределяне на консумацията по часове.

Модулът няма зависимости от Home Assistant (само pdfminer.six), за да може да
се тества отделно.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

BGN_PER_EUR = 1.95583
DEFAULT_CALORIFIC = 0.01075  # MWh/m³, ако фактурата не го посочва (2021 г.)


# ----------------------------------------------------------------- модели
@dataclass
class Segment:
    meter: str
    start: datetime  # aware, UTC
    end: datetime
    start_reading: float
    end_reading: float
    m3: float


@dataclass
class Invoice:
    number: str
    date: str
    file: str
    segments: list[Segment] = field(default_factory=list)
    calorific: float | None = None  # MWh/m³
    total_eur: float | None = None
    total_bgn: float | None = None
    eur_from_bgn: bool = False
    compensated: bool = False  # 2022 компенсации по РМС
    estimated: bool = False  # дупка между фактури, попълнена от показанията

    @property
    def m3(self) -> float:
        return sum(s.m3 for s in self.segments)

    @property
    def kwh(self) -> float:
        return self.m3 * (self.calorific or DEFAULT_CALORIFIC) * 1000

    @property
    def start(self) -> datetime:
        return min(s.start for s in self.segments)

    @property
    def end(self) -> datetime:
        return max(s.end for s in self.segments)

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400

    def as_dict(self, tz: ZoneInfo) -> dict:
        return {
            "number": self.number,
            "from": self.start.astimezone(tz).isoformat(),
            "to": self.end.astimezone(tz).isoformat(),
            "m3": round(self.m3, 2),
            "kwh": round(self.kwh, 1),
            "eur": self.total_eur,
            "eur_per_m3": round(self.total_eur / self.m3, 4) if self.m3 else None,
            "estimated": self.estimated,
        }


# ----------------------------------------------------------------- PDF -> редове текст
def pdf_lines(path) -> str:
    """Текст от PDF, подреден по редове (като pdfplumber), само с pdfminer.six.

    path може да е път или отворен binary файл."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTChar

    def walk(obj, out):
        if isinstance(obj, LTChar):
            out.append(obj)
        elif hasattr(obj, "__iter__"):
            for child in obj:
                walk(child, out)

    lines_out: list[str] = []
    for page in extract_pages(str(path) if isinstance(path, Path) else path):
        chars: list = []
        walk(page, chars)
        chars = [c for c in chars if c.size < 30]  # без големия воден знак "ОРИГИНАЛ"
        chars.sort(key=lambda c: (-round(c.y1), c.x0))
        rows: list[list] = []
        for c in chars:
            for row in rows:
                if abs(row[0] - c.y1) <= 3:
                    row[1].append(c)
                    break
            else:
                rows.append([c.y1, [c]])
        rows.sort(key=lambda r: -r[0])
        for _, row in rows:
            row.sort(key=lambda c: c.x0)
            text, last_x1 = "", None
            for c in row:
                if last_x1 is not None and c.x0 - last_x1 > 1.5 and not text.endswith(" "):
                    text += " "
                text += c.get_text()
                last_x1 = c.x1
            lines_out.append(re.sub(r"[  ]+", " ", text).strip())
    return "\n".join(lines_out)


# ----------------------------------------------------------------- парсване
NUM = r"\d+(?:[.,]\d+)?"
DT = r"\d{2}-\d{2}-\d{4} \d{2}:\d{2}(?::\d{2})?"
RE_NUMBER = re.compile(r"№\s*(\d{6,})\s*/\s*(\d{2}-\d{2}-\d{4})")
_S = r"[ \t]+"  # само интервали - полетата трябва да са на един ред
RE_METER = re.compile(
    rf"(?:([A-Za-z]+\d+){_S})?(?:([A-Za-z]+\d+){_S})?({DT}){_S}({NUM}){_S}({DT}){_S}({NUM})"
    rf"{_S}({NUM}){_S}({NUM}){_S}({NUM})[ \t]*$",
    re.M,
)
RE_CALORIFIC = re.compile(r"(\d+[.,]\d+)\s*MWh\s*/\s*m3", re.I)
MONEY = r"(\d+[.,]\d{2})"
RE_EUR = (re.compile(r"(?:€|EUR)\s*" + MONEY), re.compile(MONEY + r"\s*(?:€|EUR)"))
RE_BGN = (re.compile(r"(?:лв\.?|BGN)\s*" + MONEY), re.compile(MONEY + r"\s*(?:лв|BGN)"))


def _num(s: str) -> float:
    return float(s.replace(" ", "").replace(",", "."))


def _dt(s: str, tz: ZoneInfo) -> datetime:
    fmt = "%d-%m-%Y %H:%M:%S" if s.count(":") == 2 else "%d-%m-%Y %H:%M"
    return datetime.strptime(s, fmt).replace(tzinfo=tz).astimezone(timezone.utc)


def _first(rxs, text: str) -> float | None:
    for rx in rxs:
        if m := rx.search(text):
            return _num(m.group(1))
    return None


def parse_text(text: str, tz: ZoneInfo, filename: str = "") -> Invoice:
    m = RE_NUMBER.search(text)
    if not m:
        raise ValueError("не намирам номер на фактура")
    inv = Invoice(number=m.group(1), date=m.group(2), file=filename)

    for m in RE_METER.finditer(text):
        meter, _corr, d1, r1, d2, r2, _diff, _coef, billed = m.groups()
        inv.segments.append(
            Segment(meter or "", _dt(d1, tz), _dt(d2, tz), _num(r1), _num(r2), _num(billed))
        )
    if not inv.segments:
        raise ValueError("не намирам таблицата с показанията на разходомера")

    if c := RE_CALORIFIC.search(text):
        inv.calorific = _num(c.group(1))

    # Сума за плащане:
    #  от 08.2025: "€ 64.90 лв 126.95" или само "€ 120.98"
    #  до 07.2025: само в лева "285.05"
    #  2022: компенсация по РМС -> реално платеното е "Стойност за плащане 379.55"
    i = text.find("Сума за плащане")
    zone = text[i : i + 120] if i >= 0 else ""
    inv.total_eur = _first(RE_EUR, zone)
    if m := re.search(rf"Стойност за плащане\s*:?\s*(-?{NUM})", text):
        inv.total_bgn = _num(m.group(1))
        inv.compensated = True
    else:
        inv.total_bgn = _first(RE_BGN, zone)
        if inv.total_bgn is None and inv.total_eur is None:
            if m := re.search(rf"Сума за плащане\s*:?\s*({NUM})", text):
                inv.total_bgn = _num(m.group(1))
    if inv.total_eur is None and inv.total_bgn is not None:
        inv.total_eur = round(inv.total_bgn / BGN_PER_EUR, 2)
        inv.eur_from_bgn = True
    if inv.total_eur is None:
        raise ValueError("не намирам сума за плащане")
    return inv


def load_invoices(
    folder: Path, tz: ZoneInfo, warnings: list[str], text_cache: dict[str, str] | None = None
) -> list[Invoice]:
    """Чете всички PDF-и в папката. text_cache пази извлечения текст по
    име+размер+дата, за да не се парсват PDF-ите наново при всяко пускане."""
    found: dict[str, Invoice] = {}
    files = sorted({*folder.glob("*.pdf"), *folder.glob("*.PDF")})
    used: set[str] = set()
    for f in files:
        try:
            st = f.stat()
            key = f"{f.name}|{st.st_size}|{int(st.st_mtime)}"
            used.add(key)
            if text_cache is not None and key in text_cache:
                text = text_cache[key]
            else:
                text = pdf_lines(f)
                if text_cache is not None:
                    text_cache[key] = text
            inv = parse_text(text, tz, f.name)
        except Exception as err:  # noqa: BLE001
            warnings.append(f"{f.name}: {err}")
            continue
        if inv.number not in found:
            found[inv.number] = inv
    if text_cache is not None:
        for key in list(text_cache):
            if key not in used:
                del text_cache[key]
    invs = sorted(found.values(), key=lambda i: i.start)
    for a, b in zip(invs, invs[1:]):
        if b.start < a.end:
            warnings.append(f"застъпване на периодите: {a.number} и {b.number}")
    return invs


def find_gaps(invs: list[Invoice], warnings: list[str]) -> list[Invoice]:
    """Дупки между фактурите. При същия разходомер количеството се знае от
    показанията; цената се оценява по средната €/m³ на съседните фактури."""
    gaps = []
    for a, b in zip(invs, invs[1:]):
        if b.start <= a.end:
            continue
        sa, sb = a.segments[-1], b.segments[0]
        same_meter = not sa.meter or not sb.meter or sa.meter == sb.meter
        m3 = sb.start_reading - sa.end_reading if same_meter else -1
        if m3 < 0:
            warnings.append(
                f"дупка {a.end:%d.%m.%Y} - {b.start:%d.%m.%Y}: не може да се изчисли"
            )
            continue
        price = (a.total_eur / a.m3 + b.total_eur / b.m3) / 2 if a.m3 and b.m3 else 0.0
        cal = ((a.calorific or DEFAULT_CALORIFIC) + (b.calorific or DEFAULT_CALORIFIC)) / 2
        gap = Invoice(number="ДУПКА", date="", file="", calorific=cal,
                      total_eur=round(m3 * price, 2), estimated=True)
        gap.segments.append(
            Segment(sa.meter or sb.meter, a.end, b.start, sa.end_reading, sb.start_reading, m3)
        )
        gaps.append(gap)
    return gaps


# ----------------------------------------------------------------- разпределяне
def hour_range(start: datetime, end: datetime):
    h = start.replace(minute=0, second=0, microsecond=0)
    while h < end:
        yield h
        h += timedelta(hours=1)


def base_load_for(inv: Invoice, invs: list[Invoice]) -> float:
    """Базова консумация (топла вода, готвене) в m³/ден: най-ниската средна
    дневна консумация сред реалните фактури в рамките на ±6 месеца."""
    rates = [
        i.m3 / i.days
        for i in invs
        if not i.estimated and i.days >= 20 and abs((i.start - inv.start).days) <= 183
    ]
    return min(rates) if len(rates) >= 3 else 0.0


def distribute(
    invs: list[Invoice],
    temps: dict[datetime, float] | None,
    base_temp: float,
    base_load: float | None,
    warnings: list[str],
) -> dict[datetime, list[float]]:
    """Връща {час(UTC): [m3, kWh, EUR]}.

    base_load=None -> автоматично за всяка фактура (base_load_for).
    temps=None -> равномерно разпределение.
    """
    out: dict[datetime, list[float]] = {}
    for inv in invs:
        eur_per_m3 = inv.total_eur / inv.m3 if inv.m3 else 0.0
        kwh_per_m3 = (inv.calorific or DEFAULT_CALORIFIC) * 1000
        bl = base_load_for(inv, invs) if base_load is None else base_load
        for seg in inv.segments:
            hours = list(hour_range(seg.start, seg.end))
            frac = [
                (min(h + timedelta(hours=1), seg.end) - max(h, seg.start)).total_seconds() / 3600
                for h in hours
            ]
            total_frac = sum(frac)
            if not total_frac:
                continue
            base_total = min(bl * total_frac / 24, seg.m3)
            heat_total = seg.m3 - base_total

            weights = None
            if temps is not None:
                if all(h in temps for h in hours):
                    weights = [f * max(0.0, base_temp - temps[h]) for f, h in zip(frac, hours)]
                    if sum(weights) <= 0:
                        weights = None
                else:
                    warnings.append(f"{inv.number}: липсват температури, разпределено равномерно")
            if weights is None:
                weights = frac
            sw = sum(weights)

            for h, f, w in zip(hours, frac, weights):
                m3 = base_total * f / total_frac + heat_total * w / sw
                row = out.setdefault(h, [0.0, 0.0, 0.0])
                row[0] += m3
                row[1] += m3 * kwh_per_m3
                row[2] += m3 * eur_per_m3
    return out
