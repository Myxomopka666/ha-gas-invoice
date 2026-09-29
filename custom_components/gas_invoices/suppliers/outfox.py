"""Outfox Energy (UK). Dual-fuel statements: only the gas section is read.

Dates are whole days. A period "04 Aug 2026 to 03 Sep 2026" includes its last
day, so it ends at 00:00 on 04 Sep, where the next statement starts.
"""
from __future__ import annotations

import re
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
    period_end = day(period.group(2), tz, plus=1)
    kwh = unit_charges = 0.0
    for i, r in enumerate(rows):
        start = day(r.group(1), tz)
        end = day(rows[i + 1].group(1), tz) if i + 1 < len(rows) else period_end
        r1, r2 = unum(r.group(2)), unum(r.group(3))
        inv.segments.append(Segment("", start, end, r1, r2, round((r2 - r1) * factor, 3)))
        kwh += unum(r.group(4))
        unit_charges += unum(r.group(6))
        if "estimat" in r.group(7).lower():
            inv.estimated_read = True

    standing = sum(unum(m.group(1)) for m in RE_STANDING.finditer(section))
    net = _opt(RE_NET, section) or total
    inv.fixed_cost = round(standing * total / net, 2) if net else standing
    if inv.m3:
        inv.calorific = kwh / inv.m3 / 1000

    tariff = m.group(1).strip() if (m := RE_TARIFF.search(section)) else None
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
