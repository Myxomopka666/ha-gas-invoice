"""Masks personal data in the text of an invoice so it can be shared safely
(e.g. in a GitHub issue) when adding a new supplier.

Masked: personal/company IDs after a label, valid Bulgarian ЕГН anywhere, IBAN,
phones, e-mails, addresses, names (3 capitalised Cyrillic words, after a
customer label, or after Mr/Mrs/...), customer and account numbers, UK meter
identifiers (MPAN, MPRN, meter serial), GB VAT numbers, UK postcodes and street
addresses. Numbers, dates and amounts needed by the parsers are kept.
"""
from __future__ import annotations

import re

MASK = "████"

# этикет -> маскира се стойността след него (до края на реда или до следващия етикет)
_ID_LABELS = (
    r"Идент\.?\s*№|ЕГН|ЛНЧ|ЕИК|БУЛСТАТ|Булстат|ИН\s*по\s*ДДС|ДДС\s*№|"
    r"VAT\s*(?:Reg(?:istration)?\.?\s*)?(?:No\.?|Number)|"
    r"Клиентски\s*(?:номер|№)|Кл\.\s*№|Клиент\s*№|Абонат(?:ен|ски)?\s*(?:номер|№)|Аб\.\s*№|"
    r"ИТН|Идентификационен\s*номер(?:\s*на\s*точката)?|Партида|Договор\s*№|Сметка|IBAN|"
    r"тел\.?|Тел\.?|GSM|e-mail|Email|E-mail|имейл|"
    r"MPAN|MPRN|Serial\s*Number|Meter\s*Serial(?:\s*Number)?|Account\s*Number|"
    r"Customer\s*(?:Number|Reference)"
)
_NAME_LABELS = r"КУПУВАЧ|Купувач|Клиент|Абонат|Титуляр|Получател|ПОЛУЧАТЕЛ|Име|МОЛ"
_ADDR_LABELS = r"Адрес(?:\s*на\s*(?:обекта|потребление|кореспонденция|имота))?|Обект|Местоположение"

RE_ID = re.compile(rf"(\b(?:{_ID_LABELS})[ \t]*:?[ \t]*)([^\n]*?)(?=[ \t]+(?:{_ID_LABELS})\b|$)", re.M)
RE_ADDR = re.compile(rf"(\b(?:{_ADDR_LABELS})[ \t]*:[ \t]*)([^\n]*?)(?=[ \t]+(?:{_ADDR_LABELS})[ \t]*:|$)", re.M)
RE_NAME_AFTER = re.compile(rf"(\b(?:{_NAME_LABELS})[ \t]*:[ \t]*)([^\n:]*?)(?=[ \t]+[A-ZА-Я][^\s:]*[ \t]*:|$)", re.M)
RE_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{6,18}\b")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
RE_PHONE = re.compile(r"(?:\+359|00359)[\s-]?\d[\d\s-]{6,12}\d|(?<![\d.,])08[789][\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?![\d.,])")
# три поредни думи, започващи с главна кирилска буква (Име Презиме Фамилия)
RE_FULLNAME = re.compile(r"\b[А-Я][а-я]+(?:-[А-Я][а-я]+)?\s+[А-Я][а-я]+\s+[А-Я][а-я]+(?:-[А-Я][а-я]+)?\b")
# ред, който е само две думи с главна буква (напр. име на служител/клиент на отделен ред)
RE_NAME_LINE = re.compile(r"^[ \t]*[А-Я][а-я]+(?:-[А-Я][а-я]+)?[ \t]+[А-Я][а-я]+(?:-[А-Я][а-я]+)?[ \t]*$", re.M)
RE_10DIGITS = re.compile(r"(?<!\d)\d{10}(?!\d)")
# UK: identifiers with these labels are also masked where they appear without the label
_UK_ID_LABELS = r"MPAN|MPRN|Serial\s*Number|Meter\s*Serial(?:\s*Number)?|Account\s*Number"
RE_UK_ID = re.compile(rf"\b(?:{_UK_ID_LABELS})[ \t]*:?[ \t]*([A-Z0-9]{{6,}})\b")
RE_MPAN_SPACED = re.compile(r"\b\d{2} \d{4} \d{4} \d{3}\b")
RE_VAT_GB = re.compile(r"\bGB ?\d{3} ?\d{4} ?\d{2}(?: ?\d{3})?\b")
RE_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b")
RE_TITLE_NAME = re.compile(
    r"\b(Mr|Mrs|Ms|Miss|Mx|Dr)\.?([ \t]+)[A-Z][A-Za-z''-]+(?:[ \t]+[A-Z][A-Za-z''-]+){0,2}"
)
RE_STREET = re.compile(
    r"\b\d+[A-Za-z]?[ \t]+(?:[A-Z][a-z]+[ \t]+){1,3}"
    r"(?:Street|St|Road|Rd|Lane|Ln|Avenue|Ave|Close|Drive|Way|Court|Ct|Place|Pl|Crescent|"
    r"Gardens|Grove|Terrace|Hill|Park|Square|Mews|Row|View)\b"
)

_EGN_W = (2, 4, 8, 5, 10, 9, 7, 3, 6)


def _is_egn(s: str) -> bool:
    if len(s) != 10 or not s.isdigit():
        return False
    mm = int(s[2:4])
    if not (1 <= mm <= 12 or 21 <= mm <= 32 or 41 <= mm <= 52):
        return False
    dd = int(s[4:6])
    if not 1 <= dd <= 31:
        return False
    chk = sum(int(s[i]) * _EGN_W[i] for i in range(9)) % 11
    return (chk % 10) == int(s[9])


def _mask_value(m: re.Match) -> str:
    val = m.group(2)
    return m.group(1) + (MASK if val.strip() else val)


def redact(text: str, extra: list[str] | None = None) -> tuple[str, int]:
    """Връща (замаскиран текст, брой замени)."""
    count = 0

    def sub(rx, repl, s):
        nonlocal count
        s2, n = rx.subn(repl, s)
        count += n
        return s2

    out = text
    uk_ids = [m.group(1) for m in RE_UK_ID.finditer(text)]
    for word in [*(extra or []), *uk_ids]:
        if word and len(word) >= 3:
            out, n = re.subn(re.escape(word), MASK, out, flags=re.I)
            count += n
    out = sub(RE_EMAIL, MASK, out)
    out = sub(RE_IBAN, MASK, out)
    out = sub(RE_ID, _mask_value, out)
    out = sub(RE_ADDR, _mask_value, out)
    out = sub(RE_NAME_AFTER, _mask_value, out)
    out = sub(RE_FULLNAME, MASK, out)
    out = sub(RE_NAME_LINE, MASK, out)
    out = sub(RE_PHONE, MASK, out)
    out = sub(RE_10DIGITS, lambda m: MASK if _is_egn(m.group(0)) else m.group(0), out)
    out = sub(RE_MPAN_SPACED, MASK, out)
    out = sub(RE_VAT_GB, MASK, out)
    out = sub(RE_POSTCODE, MASK, out)
    out = sub(RE_TITLE_NAME, lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)
    out = sub(RE_STREET, MASK, out)
    return out, count
