"""Замаскиране на лични данни в текста на фактура, за да може да се сподели
безопасно (напр. в GitHub issue) при добавяне на нов доставчик.

Маскират се: ЕГН/ЛНЧ/ЕИК след етикет, валидни ЕГН навсякъде, IBAN, телефони,
имейли, адреси, имена (3 думи с главна буква или след етикет за клиент),
клиентски / абонатни номера и ИТН. Числата, датите и сумите, нужни за
парсера, остават.
"""
from __future__ import annotations

import re

MASK = "████"

# етикет -> маскира се стойността след него (до края на реда или до следващия етикет)
_ID_LABELS = (
    r"Идент\.?\s*№|ЕГН|ЛНЧ|ЕИК|БУЛСТАТ|Булстат|ИН\s*по\s*ДДС|ДДС\s*№|VAT|"
    r"Клиентски\s*(?:номер|№)|Кл\.\s*№|Клиент\s*№|Абонат(?:ен|ски)?\s*(?:номер|№)|Аб\.\s*№|"
    r"ИТН|Идентификационен\s*номер(?:\s*на\s*точката)?|Партида|Договор\s*№|Сметка|IBAN|"
    r"тел\.?|Тел\.?|GSM|e-mail|Email|E-mail|имейл"
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
    for word in extra or []:
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
    return out, count
