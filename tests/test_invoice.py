"""Тестове на парсера с анонимизиран текст (без истински лични данни)."""
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "gas_invoices"))
import invoice as inv  # noqa: E402
import redact  # noqa: E402
import pytest  # noqa: E402

TZ = ZoneInfo("Europe/Sofia")

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
    assert i.m3 == 274 and i.total_eur == 187.20 and not i.eur_from_bgn
    assert round(i.kwh) == 2943


def test_dual_currency_prefers_eur():
    i = inv.parse_text(DUAL, TZ)
    assert i.total_eur == 64.90 and i.total_bgn == 126.95


def test_bgn_only_converted():
    i = inv.parse_text(BGN_ONLY, TZ)
    assert i.total_bgn == 285.05 and i.eur_from_bgn
    assert i.total_eur == round(285.05 / inv.BGN_PER_EUR, 2)
    assert i.segments[0].meter == "" and i.calorific is None


def test_compensation_uses_paid_amount():
    i = inv.parse_text(COMPENSATED, TZ)
    assert i.compensated and i.total_bgn == 379.55


def test_meter_swap_two_segments():
    i = inv.parse_text(METER_SWAP, TZ)
    assert len(i.segments) == 2 and i.m3 == 173


def test_gap_filled_from_readings():
    a = inv.parse_text(EUR_ONLY, TZ)
    b = inv.parse_text(METER_SWAP, TZ)  # започва 01.03, а a свършва 01.02
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
    assert i.number == "0100000001" and i.m3 == 274 and i.total_eur == 187.20


def test_redact_valid_egn_anywhere():
    out, _ = redact.redact("нещо 7501020018 и 0100123131")
    assert "7501020018" not in out and "0100123131" in out
