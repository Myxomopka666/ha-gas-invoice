"""Parser tests with anonymised text (no real personal data)."""
import dataclasses
import json
import re
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "gas_invoices"))
import invoice as inv  # noqa: E402
import redact  # noqa: E402
import pytest  # noqa: E402

TZ = ZoneInfo("Europe/Sofia")
LON = ZoneInfo("Europe/London")
OUTFOX = (Path(__file__).parent / "fixtures" / "outfox_statement.txt").read_text(encoding="utf-8")

EUR_ONLY = """КОСТИНБРОДГАЗ ООД
№ 0100000001/09-02-2026
Сума за плащане: € 187.20
За текущия месец представителната калоричност е: 0.010740 MWh/m3
P00000001 01-01-2026 07:00:00 5149 01-02-2026 07:00:00 5423 274 1.000000 274.00
Всичко: 274.00
"""

DUAL = """КОСТИНБРОДГАЗ ООД
№ 0100000002/07-11-2025
Сума за плащане: € 64.90 лв 126.95
За текущия месец представителната калоричност е: 0.010790 MWh/m3
P00000001 01-10-2025 07:00:00 4691 01-11-2025 07:00:00 4793 102 1.000000 102.00
"""

BGN_ONLY = """КОСТИНБРОДГАЗ ООД
№ 0100000003/04-02-2021 г.
Сума за плащане: 285.05
Всичко (словом): ДВЕСТА ОСЕМДЕСЕТ и ПЕТ лева и 05 ст.
01-01-2021 07:00:00 503 01-02-2021 07:00:00 943 440 1.000000 440.00
"""

COMPENSATED = """КОСТИНБРОДГАЗ ООД
№ 0100000004/04-02-2022 г.
Сума за плащане: 567.56
(2.9631 MWh по 42.31 лв./MWh)
Стойност за плащане 379.55
За текущия месец представителната калоричност е: 0.010545 MWh/m3
01-01-2022 07:00:00 2105 01-02-2022 07:00:00 2386 281 1.000000 281.00
"""

METER_SWAP = """КОСТИНБРОДГАЗ ООД
№ 0100000005/08-04-2026
Сума за плащане: € 120.98
P00000001 01-03-2026 07:00:00 5627 17-03-2026 10:00:00 5711 84 1.000000 84.00
P00000002 17-03-2026 10:00:00 6450 01-04-2026 07:00:00 6539 89 1.000000 89.00
"""


def test_eur_only():
    i = inv.parse_text(EUR_ONLY, TZ)
    assert i.number == "0100000001"
    assert i.m3 == 274 and i.total == 187.20 and not i.eur_from_bgn
    assert round(i.kwh) == 2943


def test_dual_currency_prefers_eur():
    i = inv.parse_text(DUAL, TZ)
    assert i.total == 64.90 and i.total_bgn == 126.95 and i.currency == "EUR"


def test_bgn_only_converted():
    i = inv.parse_text(BGN_ONLY, TZ)
    assert i.total_bgn == 285.05 and i.eur_from_bgn
    assert i.total == round(285.05 / inv.BGN_PER_EUR, 2)
    assert i.segments[0].meter == "" and i.calorific is None


def test_compensation_uses_paid_amount():
    i = inv.parse_text(COMPENSATED, TZ)
    assert i.compensated and i.total_bgn == 379.55


def test_meter_swap_two_segments():
    i = inv.parse_text(METER_SWAP, TZ)
    assert len(i.segments) == 2 and i.m3 == 173


def test_gap_filled_from_readings():
    a = inv.parse_text(EUR_ONLY, TZ)
    b = inv.parse_text(METER_SWAP, TZ)  # starts 01.03, and a ends 01.02
    w = []
    gaps = inv.find_gaps([a, b], w)
    assert len(gaps) == 1 and gaps[0].m3 == 5627 - 5423 and gaps[0].estimated


def test_distribute_preserves_totals():
    i = inv.parse_text(EUR_ONLY, TZ)
    hours = list(inv.hour_range(i.start, i.end))
    temps = {h: 5.0 + (h.hour % 12) for h in hours}
    out = inv.distribute([i], temps, 18.0, 0.5, [])
    assert abs(sum(v[0] for v in out.values()) - 274) < 1e-6
    assert abs(sum(v[2] for v in out.values()) - 187.20) < 1e-6
    assert min(out) == i.start and max(out) == i.end - timedelta(hours=1)


def test_unknown_supplier():
    with pytest.raises(inv.UnknownSupplierError):
        inv.parse_text(EUR_ONLY.replace("КОСТИНБРОДГАЗ ООД", "ДРУГ ГАЗ АД"), TZ)


def test_supplier_is_set():
    assert inv.parse_text(EUR_ONLY, TZ).supplier == "kostinbrodgaz"


SAMPLE_PERSONAL = """ДОСТАВЧИК: КУПУВАЧ:
КОСТИНБРОДГАЗ ООД Иван Петров Иванов
Адрес: гр. София, ул. Витоша 1 Адрес: гр. Костинброд, ул. Черни връх 5
Идент. №: 131321489 Идент. №: 7501020018
тел. 072166367 тел. 0888123456
e-mail: office@example.bg e-mail: ivan@example.com
Сметка: BG10UBBS88881000909267
Клиентски номер: 101021
Мария Георгиева
"""


def test_redact_masks_personal_data():
    out, n = redact.redact(SAMPLE_PERSONAL)
    for secret in ["Иван Петров Иванов", "Витоша", "Черни връх", "7501020018", "0888123456",
                   "ivan@example.com", "BG10UBBS88881000909267", "101021", "Мария Георгиева"]:
        assert secret not in out, secret
    assert "КОСТИНБРОДГАЗ" in out and n > 5


def test_redact_keeps_invoice_data():
    out, _ = redact.redact(EUR_ONLY)
    i = inv.parse_text(out, TZ)
    assert i.number == "0100000001" and i.m3 == 274 and i.total == 187.20


def test_redact_valid_egn_anywhere():
    out, _ = redact.redact("нещо 7501020018 и 0100123131")
    assert "7501020018" not in out and "0100123131" in out


def test_bgn_converted_currency_is_eur():
    i = inv.parse_text(BGN_ONLY, TZ)
    assert i.currency == "EUR" and i.eur_from_bgn


def test_as_dict_has_currency_and_cost():
    d = inv.parse_text(EUR_ONLY, TZ).as_dict(TZ)
    assert d["cost"] == 187.20 and d["currency"] == "EUR" and d["fixed_cost"] == 0.0
    assert d["price_per_m3"] == round(187.20 / 274, 4) and d["estimated_read"] is False


def test_fixed_cost_spread_evenly():
    i = inv.parse_text(EUR_ONLY, TZ)
    i.fixed_cost = 31.0
    hours = list(inv.hour_range(i.start, i.end))
    temps = {h: (25.0 if n % 2 else 0.0) for n, h in enumerate(hours)}  # every other hour warm
    out = inv.distribute([i], temps, 18.0, 0.0, [])
    per_hour = 31.0 / len(hours)
    warm = [out[h] for n, h in enumerate(hours) if n % 2]
    assert all(r[0] == 0 and abs(r[2] - per_hour) < 1e-9 for r in warm)
    assert abs(sum(r[2] for r in out.values()) - 187.20) < 1e-6


def test_gap_estimates_fixed_cost_per_day():
    a = inv.parse_text(EUR_ONLY, TZ)
    b = inv.parse_text(METER_SWAP, TZ)
    a.fixed_cost = b.fixed_cost = 31.0  # ~1 per day
    gap = inv.find_gaps([a, b], [])[0]
    assert gap.number == "GAP" and gap.fixed_cost == pytest.approx(28, abs=0.05)
    assert gap.currency == "EUR"


def _shifted(base, number, date, days, m3, total):
    """An invoice like base, moved by `days`, with an even m3 over its period."""
    s = base.segments[0]
    seg = inv.Segment("", s.start + timedelta(days=days), s.end + timedelta(days=days), 0, m3, m3)
    return inv.Invoice(number=number, date=date, file="", segments=[seg], total=total)


def test_actual_invoice_replaces_estimated():
    actual = inv.parse_text(EUR_ONLY, TZ)
    est = dataclasses.replace(inv.parse_text(EUR_ONLY, TZ), number="0100000009", estimated_read=True)
    w = []
    out = inv.resolve_overlaps([est, actual], w)
    assert out == [actual] and "0100000009" in w[0]


def test_reissued_invoice_replaces_older_completely():
    old = inv.parse_text(EUR_ONLY, TZ)  # issued 09-02-2026
    new = dataclasses.replace(old, number="0100000009", date="20-02-2026", total=150.0)
    w = []
    assert inv.resolve_overlaps([old, new], w) == [new] and "0100000001" in w[0]


def test_newer_invoice_wins_partial_overlap():
    old = inv.parse_text(EUR_ONLY, TZ)  # 01.01-01.02, issued 09-02-2026
    new = _shifted(old, "0100000009", "20-02-2026", 14, 100.0, 50.0)  # 15.01-15.02
    out = inv.resolve_overlaps([old, new], [])
    assert out == [old, new] and old.superseded == [(new.start, old.end)] and not new.superseded
    hourly = inv.distribute(out, None, 18.0, 0.0, [])
    n_old = len(list(inv.hour_range(old.start, old.end)))
    n_new = len(list(inv.hour_range(new.start, new.end)))
    before = new.start - timedelta(hours=1)
    assert hourly[before][0] == pytest.approx(274 / n_old)  # only the old invoice
    assert hourly[new.start][0] == pytest.approx(100 / n_new)  # only the new invoice


def test_newer_issue_date_wins_even_for_earlier_period():
    later_period = _shifted(inv.parse_text(EUR_ONLY, TZ), "0100000008", "20-02-2026", 14, 100.0, 50.0)
    correction = dataclasses.replace(inv.parse_text(EUR_ONLY, TZ), date="25-02-2026")  # 01.01-01.02
    inv.resolve_overlaps([correction, later_period], [])
    assert later_period.superseded == [(later_period.start, correction.end)]
    assert not correction.superseded


def test_touching_invoices_do_not_overlap():
    a = inv.parse_text(EUR_ONLY, TZ)
    b = _shifted(a, "0100000009", "20-03-2026", 31, 100.0, 50.0)  # starts exactly at a.end
    w = []
    assert inv.resolve_overlaps([a, b], w) == [a, b] and not w and not a.superseded


def _span(number, date, start, end, m3=100.0, estimated_read=False, reading0=0.0):
    """An invoice with one segment between two dates (day of month, 2026)."""
    from datetime import datetime

    def at(d):
        return datetime(2026, d[0], d[1], tzinfo=TZ)

    seg = inv.Segment("", at(start), at(end), reading0, reading0 + m3, m3)
    return inv.Invoice(
        number=number, date=date, file="", segments=[seg], total=m3, estimated_read=estimated_read
    )


def test_no_hole_from_window_given_to_a_dropped_invoice():
    x = _span("X", "01-02-2026", (1, 1), (1, 11), estimated_read=True)
    a = _span("A", "05-02-2026", (1, 8), (1, 20), estimated_read=True)
    d = _span("D", "10-02-2026", (1, 15), (2, 1))
    w = []
    out = inv.resolve_overlaps([x, a, d], w)
    assert out == [x, d] and x.superseded == [] and any("A:" in m for m in w)


def test_nested_invoice_does_not_create_a_false_gap():
    old = _span("O", "01-02-2026", (1, 1), (2, 1), reading0=0.0)
    nested = _span("N", "05-02-2026", (1, 11), (1, 21), reading0=30.0)
    c = _span("C", "06-02-2026", (2, 1), (3, 1), reading0=100.0)
    w = []
    assert inv.find_gaps([old, nested, c], w) == [] and not w


def test_equal_issue_date_later_start_wins():
    a = _span("A", "10-02-2026", (1, 1), (1, 20))
    b = _span("B", "10-02-2026", (1, 10), (2, 1))
    inv.resolve_overlaps([a, b], [])
    assert a.superseded == [(b.start, a.end)] and not b.superseded


def test_outfox_gas_only():
    i = inv.parse_text(OUTFOX, LON)
    assert i.supplier == "outfox" and i.number == "12345678" and i.date == "05-09-2026"
    assert i.currency == "GBP" and i.total == 17.52 and i.fixed_cost == 9.06
    assert i.m3 == 12.0 and round(i.kwh, 1) == 138.4 and not i.estimated_read


def test_outfox_period_is_whole_days():
    i = inv.parse_text(OUTFOX, LON)
    assert i.start.astimezone(LON).isoformat() == "2026-08-04T00:00:00+01:00"
    assert i.end.astimezone(LON).isoformat() == "2026-09-04T00:00:00+01:00"
    assert i.days == 31


def test_outfox_extras():
    e = inv.parse_text(OUTFOX, LON).extras
    assert e["standing_charge"] == 8.63 and e["vat"] == 0.83 and e["unit_charges"] == 8.06
    assert e["calorific_value_mj_m3"] == 40.6 and e["volume_correction"] == 1.02264
    assert e["tariff"] == "Fix'd Dual Jun26 12M v5" and e["kwh_billed"] == 138.4


def test_outfox_estimated_read():
    text = OUTFOX.replace(
        "- your read your read\n02 Sep 26 Serial", "- estimated read estimated read\n02 Sep 26 Serial"
    )
    assert inv.parse_text(text, LON).estimated_read


def test_outfox_imperial_meter():
    text = OUTFOX.replace("Volume conversion factor × 1.0", "Volume conversion factor × 2.83")
    assert round(inv.parse_text(text, LON).m3, 2) == 33.96


def test_outfox_number_falls_back_to_statement_date():
    i = inv.parse_text(OUTFOX.replace("Statement Number: 12345678", "Statement Number: ████"), LON)
    assert i.number == "20260905"


SAMPLE_UK = """BALANCE £437.10 CR
Account Number: 87654321
Mr John Smith
12 High Street Statement Number: 12345678
Anytown AB1 2CD Statement Date: 05/09/2026
Hi Mr John Smith, Don’t forget to send us your meter readings
- your read your read 16 0000 0000 001
MPAN 1600000000001
02 Sep 26 00 84975.0 84975.0 0.0kWh 20.64074p £0.00 Serial Number AB12C34567
reading reading 1234567890
MPRN 1234567890
VAT £0.83
Company registration number 09689035, VAT Reg No: GB123456789
"""


def test_redact_uk_personal_data():
    out, _ = redact.redact(SAMPLE_UK)
    for secret in ["87654321", "John", "Smith", "High Street", "AB1 2CD", "16 0000 0000 001",
                   "1600000000001", "AB12C34567", "1234567890", "GB123456789"]:
        assert secret not in out, secret
    assert "VAT £0.83" in out and "Statement Number: 12345678" in out


def test_redact_title_name_with_curly_apostrophe():
    out, _ = redact.redact("Dear Mr O’Brien Smith,")
    assert "Brien" not in out and "Smith" not in out and out.startswith("Dear Mr ")


def test_redact_keeps_outfox_data():
    out, _ = redact.redact(OUTFOX)
    i = inv.parse_text(out, LON)
    assert i.number == "12345678" and i.m3 == 12.0 and i.total == 17.52 and i.fixed_cost == 9.06


BASE = Path(__file__).parents[1] / "custom_components" / "gas_invoices"


def _load(name):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def _keys(d, p=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out |= _keys(v, f"{p}{k}.")
        else:
            out[p + k] = set(re.findall(r"\{(\w+)\}", v))
    return out


def test_translations_match():
    strings = _load("strings.json")
    assert _load("translations/en.json") == strings
    assert _keys(_load("translations/bg.json")) == _keys(strings)  # same keys and placeholders


def test_parser_errors_in_english():
    with pytest.raises(inv.UnknownSupplierError, match="unknown supplier"):
        inv.parse_text("nothing here", TZ)
    with pytest.raises(ValueError, match="invoice number not found"):
        inv.parse_text("КОСТИНБРОДГАЗ ООД", TZ)


# ------------------------------------------------------------ final review fixes
def test_outfox_imperial_gap_volume_in_m3():
    text = OUTFOX.replace("Volume conversion factor × 1.0", "Volume conversion factor × 2.83")
    a = inv.parse_text(text, LON)
    s = a.segments[0]
    later = timedelta(days=33)  # a gap of 2 days after a.end
    # the next statement starts reading 6 raw units after 22894.0
    seg = inv.Segment("", s.start + later, s.end + later, 22900.0 * 2.83, 22912.0 * 2.83, 33.96)
    b = dataclasses.replace(a, number="99999999", segments=[seg])
    w = []
    gaps = inv.find_gaps([a, b], w)
    assert len(gaps) == 1 and gaps[0].segments[0].m3 == pytest.approx(6 * 2.83, abs=0.01)


def test_outfox_meter_serial_used_as_meter_id():
    text = OUTFOX.replace("02 Sep 26 Serial Number ████", "02 Sep 26 Serial Number G4A1234567")
    assert inv.parse_text(text, LON).segments[0].meter == "G4A1234567"
    assert inv.parse_text(OUTFOX, LON).segments[0].meter == ""


def test_invoice_covered_by_union_of_newer_invoices_is_dropped():
    old = _span("OLD", "01-02-2026", (1, 1), (1, 21))
    n1 = _span("N1", "10-02-2026", (1, 1), (1, 11))
    n2 = _span("N2", "11-02-2026", (1, 10), (1, 21))
    w = []
    out = inv.resolve_overlaps([old, n1, n2], w)
    assert out == [n1, n2] and "OLD: replaced by newer invoices" in w


def test_redact_title_name_does_not_swallow_label():
    out, _ = redact.redact("Mrs Jane Doe Statement Date: 05/09/2026")
    assert "Statement Date: 05/09/2026" in out and "Jane" not in out and "Doe" not in out


def test_redact_uk_phone_numbers():
    out, _ = redact.redact("Call 07700 900123 or +44 20 7946 0958 or 020 7946 0958 today")
    assert "900123" not in out and "7946" not in out
