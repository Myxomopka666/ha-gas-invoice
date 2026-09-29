"""Константи за Gas Invoices."""
from __future__ import annotations

DOMAIN = "gas_invoices"
SERVICE_IMPORT = "import_invoices"
SERVICE_DEBUG = "debug_invoice"
EVENT_IMPORTED = f"{DOMAIN}_imported"
SIGNAL_UPDATED = f"{DOMAIN}_updated_{{}}"  # .format(entry_id)

STAT_M3 = f"{DOMAIN}:consumption"
STAT_KWH = f"{DOMAIN}:energy"
STAT_COST = f"{DOMAIN}:cost"

CONF_FOLDER = "folder"
CONF_BASE_TEMP = "base_temp"
CONF_BASE_LOAD = "base_load"
CONF_FILL_GAPS = "fill_gaps"
CONF_WEATHER = "weather"
CONF_DAILY = "daily"
CONF_RESET = "reset"
CONF_FILE = "file"
CONF_MASK = "mask"
ISSUE_URL = "https://github.com/Myxomopka666/ha-gas-invoice/issues/new?template=new_supplier.yml"

DEFAULT_FOLDER_NAME = "gas_invoices"  # под /config
DEFAULTS = {
    CONF_BASE_TEMP: 18.0,
    CONF_BASE_LOAD: "auto",
    CONF_FILL_GAPS: True,
    CONF_WEATHER: True,
    CONF_DAILY: True,
}
DAILY_AT = (3, 15)  # час, минута за автоматичния импорт
