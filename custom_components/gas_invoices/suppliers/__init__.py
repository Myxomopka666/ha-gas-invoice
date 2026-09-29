"""Парсери по доставчици.

За нов доставчик: добави модул с KEY, NAME, detect(text) -> bool и
parse(text, tz, filename) -> Invoice, и го сложи в SUPPLIERS.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from . import kostinbrodgaz, outfox
from .base import BGN_PER_EUR, DEFAULT_CALORIFIC, Invoice, Segment

__all__ = ["BGN_PER_EUR", "DEFAULT_CALORIFIC", "Invoice", "Segment", "SUPPLIERS", "UnknownSupplierError", "detect", "parse", "supported_names"]

SUPPLIERS = [kostinbrodgaz, outfox]


class UnknownSupplierError(ValueError):
    """Фактурата е от доставчик, който още не се поддържа."""


def detect(text: str):
    for sup in SUPPLIERS:
        if sup.detect(text):
            return sup
    return None


def supported_names() -> list[str]:
    return [s.NAME for s in SUPPLIERS]


def parse(text: str, tz: ZoneInfo, filename: str = "") -> Invoice:
    sup = detect(text)
    if sup is None:
        raise UnknownSupplierError(
            "непознат доставчик (поддържани: " + ", ".join(supported_names()) + ")"
        )
    return sup.parse(text, tz, filename)
