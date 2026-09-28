"""Четене на PDF фактури за газ и разпределяне на консумацията по часове.

Парсерите за отделните доставчици са в пакета suppliers.

Модулът няма зависимости от Home Assistant (само pdfminer.six), за да може да
се тества отделно.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

try:  # като част от интеграцията
    from . import suppliers
except ImportError:  # тестове / самостоятелно ползване
    import suppliers  # type: ignore[no-redef]

Invoice = suppliers.Invoice
Segment = suppliers.Segment
BGN_PER_EUR = suppliers.BGN_PER_EUR
DEFAULT_CALORIFIC = suppliers.DEFAULT_CALORIFIC
UnknownSupplierError = suppliers.UnknownSupplierError


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
def parse_text(text: str, tz: ZoneInfo, filename: str = "") -> Invoice:
    """Разпознава доставчика по текста и парсва с неговия парсер."""
    return suppliers.parse(text, tz, filename)


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
