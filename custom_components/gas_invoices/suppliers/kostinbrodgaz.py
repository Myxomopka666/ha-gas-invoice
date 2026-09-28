"""КОСТИНБРОДГАЗ ООД (Костинброд). Всички формати от 2021 г. насам:
само лева, лева с компенсации по РМС (2022), евро + лева (от 08.2025), само евро (от 2026)."""
from __future__ import annotations

import re
from zoneinfo import ZoneInfo

from .base import NUM, RE_BGN, RE_CALORIFIC, RE_EUR, Invoice, Segment, first, local_dt, num, to_eur

KEY = "kostinbrodgaz"
NAME = "Костинбродгаз"

DT = r"\d{2}-\d{2}-\d{4} \d{2}:\d{2}(?::\d{2})?"
_S = r"[ \t]+"  # само интервали - полетата трябва да са на един ред
RE_NUMBER = re.compile(r"№\s*(\d{6,})\s*/\s*(\d{2}-\d{2}-\d{4})")
RE_METER = re.compile(
    rf"(?:([A-Za-z]+\d+){_S})?(?:([A-Za-z]+\d+){_S})?({DT}){_S}({NUM}){_S}({DT}){_S}({NUM})"
    rf"{_S}({NUM}){_S}({NUM}){_S}({NUM})[ \t]*$",
    re.M,
)


def detect(text: str) -> bool:
    return "КОСТИНБРОДГАЗ" in text.upper()


def parse(text: str, tz: ZoneInfo, filename: str = "") -> Invoice:
    m = RE_NUMBER.search(text)
    if not m:
        raise ValueError("не намирам номер на фактура")
    inv = Invoice(number=m.group(1), date=m.group(2), file=filename, supplier=KEY)

    for m in RE_METER.finditer(text):
        meter, _corr, d1, r1, d2, r2, _diff, _coef, billed = m.groups()
        inv.segments.append(
            Segment(meter or "", local_dt(d1, tz), local_dt(d2, tz), num(r1), num(r2), num(billed))
        )
    if not inv.segments:
        raise ValueError("не намирам таблицата с показанията на разходомера")

    if c := RE_CALORIFIC.search(text):
        inv.calorific = num(c.group(1))

    # Сума за плащане:
    #  от 08.2025: "€ 64.90 лв 126.95" или само "€ 120.98"
    #  до 07.2025: само в лева "285.05"
    #  2022: компенсация по РМС -> реално платеното е "Стойност за плащане 379.55"
    i = text.find("Сума за плащане")
    zone = text[i : i + 120] if i >= 0 else ""
    inv.total_eur = first(RE_EUR, zone)
    if m := re.search(rf"Стойност за плащане\s*:?\s*(-?{NUM})", text):
        inv.total_bgn = num(m.group(1))
        inv.compensated = True
    else:
        inv.total_bgn = first(RE_BGN, zone)
        if inv.total_bgn is None and inv.total_eur is None:
            if m := re.search(rf"Сума за плащане\s*:?\s*({NUM})", text):
                inv.total_bgn = num(m.group(1))
    to_eur(inv)
    if inv.total_eur is None:
        raise ValueError("не намирам сума за плащане")
    return inv
