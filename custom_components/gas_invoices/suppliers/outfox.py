"""Outfox Energy (UK). Dual-fuel statements: only the gas section is read.

A rate change mid-period splits the gas section into sub-sections, each with
its own period, rows, standing charge and net; VAT and the total come once, last.

Dates are whole days. A period "04 Aug 2026 to 03 Sep 2026" includes its last
day, so it ends at 00:00 on 04 Sep, where the next statement starts.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .base import Invoice, Segment

KEY = "outfox"
NAME = "Outfox Energy"

MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
UNUM = r"\d[\d,]*(?:\.\d+)?"  # 22,882.0
DAY = r"\d{2} [A-Z][a-z]{2} \d{2}(?:\d{2})?"  # 04 Aug 26 / 04 Aug 2026

RE_GAS = re.compile(rf"Your Charges - Gas(.*?)Total Gas Charges For Period[ \t]*£({UNUM})", re.S)
RE_PERIOD = re.compile(rf"\(({DAY}) to ({DAY})\)")
RE_TARIFF = re.compile(rf"^(.+?)[ \t]*\({DAY} to {DAY}\)", re.M)
RE_ROW = re.compile(
    rf"^({DAY})[ \t]+({UNUM})[ \t]+({UNUM})[ \t]+({UNUM})[ \t]*kWh[ \t]+({UNUM})p[ \t]+£({UNUM})[^\n]*\n"
    rf"-[ \t]*([^\n]*)\n"
    rf"({DAY})",
    re.M,
)
RE_STANDING = re.compile(rf"Standing charge \(\d+ days @ {UNUM}p per day\)[ \t]*£({UNUM})")
RE_NET = re.compile(rf"Net Gas Charges For Period[ \t]*£({UNUM})")
RE_FACTOR = re.compile(rf"Volume conversion factor[ \t]*[×x][ \t]*({UNUM})")
RE_CORRECTION = re.compile(rf"Volume correction[ \t]*[×x][ \t]*({UNUM})")
RE_CV = re.compile(rf"Calorific value[ \t]*[×x][ \t]*({UNUM})")
RE_READ = re.compile(r"your read|estimat", re.I)  # "- <previous> <latest>"
RE_SERIAL =re.compile(r"Serial Number[ 	]+([A-Z0-9]{5,})")
RE_STATEMENT = re.compile(r"Statement Number:[ \t]*(\d+)")
RE_STATEMENT_DATE = re.compile(r"Statement Date:[ \t]*(\d{2})/(\d{2})/(\d{4})")


def unum(s: str) -> float:
    return float(s.replace(",", ""))


def day(s: str, tz: ZoneInfo, plus: int = 0) -> datetime:
    """'04 Aug 26' -> local midnight (+ plus days) as aware UTC."""
    d, mon, y = s.split()
    year = int(y) + 2000 if len(y) == 2 else int(y)
    local = datetime(year, MONTHS[mon[:3].lower()], int(d)) + timedelta(days=plus)
    return local.replace(tzinfo=tz).astimezone(timezone.utc)


def _opt(rx: re.Pattern, text: str) -> float | None:
    return unum(m.group(1)) if (m := rx.search(text)) else None


def detect(text: str) -> bool:
    return "outfox" in text.lower()


def _price_segments(inv: Invoice, section: str, rows: list, standings: list) -> None:
    """Each row's own unit charge, plus its sub-section's standing charge split by
    days; VAT (printed once, for the whole section) is added in proportion."""
    starts = [m.start() for m in RE_PERIOD.finditer(section)] + [len(section)]
    by_sub: dict[int, list[int]] = {}
    for i, r in enumerate(rows):
        by_sub.setdefault(bisect_right(starts, r.start()) - 1, []).append(i)
    fixed = [0.0] * len(rows)
    for k, idx in by_sub.items():
        amount = sum(unum(m.group(1)) for m in standings if starts[k] <= m.start() < starts[k + 1])
        days = [inv.segments[i].end - inv.segments[i].start for i in idx]
        span = sum(d.total_seconds() for d in days)
        for i, d in zip(idx, days):
            fixed[i] = amount * d.total_seconds() / span if span else amount / len(idx)
    net = sum(unum(r.group(6)) for r in rows) + sum(fixed)
    vat = (inv.total or 0.0) / net if net else 1.0
    for seg, r, f in zip(inv.segments, rows, fixed):
        seg.variable_cost = unum(r.group(6)) * vat
        seg.fixed_cost = f * vat


def parse(text: str, tz: ZoneInfo, filename: str = "") -> Invoice:
    gas = RE_GAS.search(text)
    if not gas:
        raise ValueError("gas section not found")
    section, total = gas.group(1), unum(gas.group(2))
    period = RE_PERIOD.search(section)
    if not period:
        raise ValueError("billing period not found")
    rows = list(RE_ROW.finditer(section))
    if not rows:
        raise ValueError("gas meter readings not found")

    number = m.group(1) if (m := RE_STATEMENT.search(text)) else ""
    date = ""
    if m := RE_STATEMENT_DATE.search(text):
        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        number = number or f"{m.group(3)}{m.group(2)}{m.group(1)}"
    if not number:
        raise ValueError("statement number not found")

    inv = Invoice(
        number=number, date=date, file=filename, supplier=KEY, currency="GBP", total=total
    )
    factor = _opt(RE_FACTOR, text) or 1.0
    meter = m.group(1) if (m := RE_SERIAL.search(section)) else ""
    period_end = day(list(RE_PERIOD.finditer(section))[-1].group(2), tz, plus=1)
    kwh = unit_charges = 0.0
    for i, r in enumerate(rows):
        start = day(r.group(1), tz)
        end = day(rows[i + 1].group(1), tz) if i + 1 < len(rows) else period_end
        r1, r2 = unum(r.group(2)), unum(r.group(3))
        inv.segments.append(
            Segment(meter, start, end, r1 * factor, r2 * factor, round((r2 - r1) * factor, 3))
        )
        kwh += unum(r.group(4))
        unit_charges += unum(r.group(6))
    # Only the closing reading counts: an estimate at a rate change just splits the period.
    reads = RE_READ.findall(rows[-1].group(7))
    inv.estimated_read = bool(reads) and reads[-1].lower() == "estimat"

    standings = list(RE_STANDING.finditer(section))
    standing = sum(unum(m.group(1)) for m in standings)
    net = sum(unum(m.group(1)) for m in RE_NET.finditer(section)) or total
    inv.fixed_cost = round(standing * total / net, 2) if net else standing
    _price_segments(inv, section, rows, standings)
    if inv.m3:
        inv.calorific = kwh / inv.m3 / 1000

    tariffs = dict.fromkeys(m.group(1).strip() for m in RE_TARIFF.finditer(section))
    tariff = " / ".join(tariffs) or None
    extras = {
        "kwh_billed": round(kwh, 1),
        "unit_charges": round(unit_charges, 2),
        "standing_charge": round(standing, 2),
        "vat": round(total - net, 2),
        "volume_conversion_factor": factor,
        "volume_correction": _opt(RE_CORRECTION, text),
        "calorific_value_mj_m3": _opt(RE_CV, text),
        "tariff": tariff,
    }
    inv.extras = {k: v for k, v in extras.items() if v is not None}
    return inv
