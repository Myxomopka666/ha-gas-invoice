"""Shared models and helpers for supplier parsers (no Home Assistant dependencies)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BGN_PER_EUR = 1.95583
DEFAULT_CALORIFIC = 0.01075  # MWh/m³, when the invoice does not state it


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
    total: float | None = None  # amount due, in `currency`
    currency: str = "EUR"  # ISO 4217 code
    fixed_cost: float = 0.0  # part of `total` independent of consumption (standing charge)
    total_bgn: float | None = None
    eur_from_bgn: bool = False
    compensated: bool = False  # 2022 government compensation (Bulgaria)
    estimated: bool = False  # gap between invoices, filled from meter readings
    estimated_read: bool = False  # the supplier billed an estimated meter reading
    supplier: str = ""
    extras: dict = field(default_factory=dict)  # supplier-specific details, informational only

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

    @property
    def variable_cost(self) -> float:
        """The part of the amount that follows consumption."""
        return (self.total or 0.0) - self.fixed_cost

    def as_dict(self, tz: ZoneInfo) -> dict:
        return {
            "number": self.number,
            "supplier": self.supplier,
            "from": self.start.astimezone(tz).isoformat(),
            "to": self.end.astimezone(tz).isoformat(),
            "m3": round(self.m3, 2),
            "kwh": round(self.kwh, 1),
            "cost": self.total,
            "currency": self.currency,
            "fixed_cost": round(self.fixed_cost, 2),
            "price_per_m3": (
                round(self.total / self.m3, 4) if self.m3 and self.total is not None else None
            ),
            "estimated": self.estimated,
            "estimated_read": self.estimated_read,
            "extras": self.extras,
        }


# --- shared regular expressions and helpers
NUM = r"\d+(?:[.,]\d+)?"
MONEY = r"(\d+[.,]\d{2})"
RE_EUR = (re.compile(r"(?:€|EUR)\s*" + MONEY), re.compile(MONEY + r"\s*(?:€|EUR)"))
RE_BGN = (re.compile(r"(?:лв\.?|BGN)\s*" + MONEY), re.compile(MONEY + r"\s*(?:лв|BGN)"))
RE_CALORIFIC = re.compile(r"(\d+[.,]\d+)\s*MWh\s*/\s*m3", re.I)


def num(s: str) -> float:
    return float(s.replace(" ", "").replace(",", "."))


def local_dt(s: str, tz: ZoneInfo, fmts=("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M")) -> datetime:
    """Local time from the invoice -> aware UTC."""
    for fmt in fmts:
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=tz).astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unknown date format: {s}")


def first(rxs, text: str) -> float | None:
    for rx in rxs:
        if m := rx.search(text):
            return num(m.group(1))
    return None


def to_eur(inv: Invoice) -> None:
    """If the amount is only in BGN, convert it at the fixed euro rate."""
    if inv.total is None and inv.total_bgn is not None:
        inv.total = round(inv.total_bgn / BGN_PER_EUR, 2)
        inv.currency = "EUR"
        inv.eur_from_bgn = True
