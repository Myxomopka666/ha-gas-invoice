"""Reading PDF gas invoices and distributing consumption by hour.

The per-supplier parsers are in the suppliers package.

The module has no Home Assistant dependencies (only pdfminer.six), so it can be
tested on its own.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

try:  # as part of the integration
    from . import suppliers
except ImportError:  # tests / standalone use
    import suppliers  # type: ignore[no-redef]

Invoice = suppliers.Invoice
Segment = suppliers.Segment
BGN_PER_EUR = suppliers.BGN_PER_EUR
DEFAULT_CALORIFIC = suppliers.DEFAULT_CALORIFIC
UnknownSupplierError = suppliers.UnknownSupplierError


# ----------------------------------------------------------------- PDF -> lines of text
def pdf_lines(path) -> str:
    """Text from a PDF arranged in lines (like pdfplumber), using only pdfminer.six.

    path can be a path or an open binary file."""
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
        chars = [c for c in chars if c.size < 30]  # without the large "ОРИГИНАЛ" watermark
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


# ----------------------------------------------------------------- parsing
def parse_text(text: str, tz: ZoneInfo, filename: str = "") -> Invoice:
    """Detects the supplier from the text and parses with its parser."""
    return suppliers.parse(text, tz, filename)


def load_invoices(
    folder: Path, tz: ZoneInfo, warnings: list[str], text_cache: dict[str, str] | None = None
) -> list[Invoice]:
    """Reads all PDFs in the folder. text_cache keeps the extracted text by
    name+size+date, so the PDFs aren't parsed again on every run."""
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
    return resolve_overlaps(invs, warnings)


def _issued(inv: Invoice) -> datetime:
    try:
        return datetime.strptime(inv.date, "%d-%m-%Y")
    except ValueError:
        return datetime.min


def resolve_overlaps(invs: list[Invoice], warnings: list[str]) -> list[Invoice]:
    """Resolve overlapping invoices (invs sorted by start), keeping their order.
    1. An estimated-read invoice overlapping an actual-read one is dropped.
    2. Among the rest, the later issue date (then later start) wins the overlap:
       a fully covered older invoice is dropped, otherwise it keeps only the
       hours outside the overlap (`superseded` windows)."""
    def overlap(a: Invoice, b: Invoice) -> bool:
        return a.start < b.end and b.start < a.end

    actual = [i for i in invs if not i.estimated_read]
    kept = []
    for x in invs:
        rival = next((a for a in actual if x.estimated_read and overlap(x, a)), None)
        if rival:
            warnings.append(f"{x.number}: estimated reading replaced by invoice {rival.number}")
        else:
            kept.append(x)

    dropped: set[int] = set()
    ranked = sorted(kept, key=lambda i: (_issued(i), i.start), reverse=True)
    for n, old in enumerate(ranked):
        for new in ranked[:n]:
            if id(new) in dropped or not overlap(new, old):
                continue
            if new.start <= old.start and new.end >= old.end:
                dropped.add(id(old))
                warnings.append(f"{old.number}: replaced by newer invoice {new.number}")
                break
            start, end = max(new.start, old.start), min(new.end, old.end)
            old.superseded.append((start, end))
            warnings.append(
                f"{old.number}: {start:%d.%m.%Y} - {end:%d.%m.%Y} replaced by newer invoice {new.number}"
            )
    return [i for i in kept if id(i) not in dropped]


def find_gaps(invs: list[Invoice], warnings: list[str]) -> list[Invoice]:
    """Gaps between invoices. With the same meter the volume is known from the
    readings; the price and the fixed cost per day are averaged from the
    neighbouring invoices."""
    def gap_between(a: Invoice, b: Invoice) -> Invoice | None:
        sa, sb = a.segments[-1], b.segments[0]
        same_meter = not sa.meter or not sb.meter or sa.meter == sb.meter
        m3 = sb.start_reading - sa.end_reading if same_meter else -1
        if m3 < 0:
            warnings.append(f"gap {a.end:%d.%m.%Y} - {b.start:%d.%m.%Y}: cannot be calculated")
            return None
        price = (a.variable_cost / a.m3 + b.variable_cost / b.m3) / 2 if a.m3 and b.m3 else 0.0
        fixed_per_day = (
            (a.fixed_cost / a.days + b.fixed_cost / b.days) / 2 if a.days and b.days else 0.0
        )
        fixed = round(fixed_per_day * (b.start - a.end).total_seconds() / 86400, 2)
        cal = ((a.calorific or DEFAULT_CALORIFIC) + (b.calorific or DEFAULT_CALORIFIC)) / 2
        gap = Invoice(
            number="GAP", date="", file="", calorific=cal, total=round(m3 * price + fixed, 2),
            currency=a.currency, fixed_cost=fixed, estimated=True,
        )
        gap.segments.append(
            Segment(sa.meter or sb.meter, a.end, b.start, sa.end_reading, sb.start_reading, m3)
        )
        return gap

    gaps: list[Invoice] = []
    if not invs:
        return gaps
    a = invs[0]  # the invoice reaching furthest so far
    for b in invs[1:]:
        if b.start > a.end and (gap := gap_between(a, b)):
            gaps.append(gap)
        if b.end > a.end:
            a = b
    return gaps


# ----------------------------------------------------------------- distribution
def hour_range(start: datetime, end: datetime):
    h = start.replace(minute=0, second=0, microsecond=0)
    while h < end:
        yield h
        h += timedelta(hours=1)


def base_load_for(inv: Invoice, invs: list[Invoice]) -> float:
    """Base consumption (hot water, cooking) in m³/day: the lowest average
    daily consumption among the real invoices within ±6 months."""
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
    """Returns {hour (UTC): [m3, kWh, cost]}.

    Variable cost follows the gas volume; the fixed cost (standing charge) is
    spread evenly over the invoice period.
    base_load=None -> automatic per invoice (base_load_for).
    temps=None -> even distribution.
    """
    out: dict[datetime, list[float]] = {}
    for inv in invs:
        kwh_per_m3 = (inv.calorific or DEFAULT_CALORIFIC) * 1000
        bl = base_load_for(inv, invs) if base_load is None else base_load
        parts = []
        for seg in inv.segments:
            hours = list(hour_range(seg.start, seg.end))
            frac = [
                (min(h + timedelta(hours=1), seg.end) - max(h, seg.start)).total_seconds() / 3600
                for h in hours
            ]
            parts.append((seg, hours, frac))
        inv_hours = sum(sum(frac) for _, _, frac in parts)
        if not inv_hours:
            continue
        total = inv.total or 0.0
        fixed = inv.fixed_cost if inv.m3 else total
        cost_per_m3 = (total - fixed) / inv.m3 if inv.m3 else 0.0
        fixed_per_hour = fixed / inv_hours

        for seg, hours, frac in parts:
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
                    warnings.append(f"{inv.number}: temperatures missing, distributed evenly")
            if weights is None:
                weights = frac
            sw = sum(weights)

            for h, f, w in zip(hours, frac, weights):
                if any(s <= h < e for s, e in inv.superseded):
                    continue
                m3 = base_total * f / total_frac + heat_total * w / sw
                row = out.setdefault(h, [0.0, 0.0, 0.0])
                row[0] += m3
                row[1] += m3 * kwh_per_m3
                row[2] += m3 * cost_per_m3 + f * fixed_per_hour
    return out
