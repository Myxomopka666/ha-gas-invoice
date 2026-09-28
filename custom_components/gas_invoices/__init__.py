"""Gas Invoices - газ от PDF фактури като статистики за Energy таблото.

- Качване на PDF/ZIP от UI (Settings -> Devices & services -> Gas Invoices -> Configure)
- Бутон "Импорт" и сензори за последната фактура
- Действие gas_invoices.import_invoices (за автоматизации / Node-RED)
- Автоматичен импорт всяка нощ (по избор)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BASE_LOAD,
    CONF_BASE_TEMP,
    CONF_DAILY,
    CONF_FILL_GAPS,
    CONF_FOLDER,
    CONF_RESET,
    CONF_WEATHER,
    DAILY_AT,
    DEFAULTS,
    DOMAIN,
    EVENT_IMPORTED,
    SERVICE_IMPORT,
    SIGNAL_UPDATED,
)
from .importer import async_import

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BUTTON, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_RESET, default=False): cv.boolean,
        vol.Optional(CONF_FOLDER): cv.string,
        vol.Optional(CONF_BASE_TEMP): vol.Coerce(float),
        vol.Optional(CONF_BASE_LOAD): cv.string,
        vol.Optional(CONF_FILL_GAPS): cv.boolean,
        vol.Optional(CONF_WEATHER): cv.boolean,
    }
)


@dataclass
class GasInvoicesData:
    store: Store
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_result: dict | None = None
    last_error: str | None = None


type GasInvoicesConfigEntry = ConfigEntry[GasInvoicesData]


def entry_options(entry: ConfigEntry) -> dict:
    return {**DEFAULTS, **entry.data, **entry.options}


async def async_run_import(
    hass: HomeAssistant, entry: GasInvoicesConfigEntry, overrides: dict | None = None
) -> dict:
    """Пуска импорта (един по един) и уведомява сензорите."""
    data = entry.runtime_data
    async with data.lock:
        try:
            result = await async_import(
                hass, data.store, {**entry_options(entry), **(overrides or {})}
            )
        except HomeAssistantError as err:
            data.last_error = str(err)
            async_dispatcher_send(hass, SIGNAL_UPDATED.format(entry.entry_id))
            raise
    data.last_result, data.last_error = result, None
    async_dispatcher_send(hass, SIGNAL_UPDATED.format(entry.entry_id))
    hass.bus.async_fire(EVENT_IMPORTED, result)
    return result


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def handle_import(call: ServiceCall) -> ServiceResponse:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise HomeAssistantError("Gas Invoices не е конфигурирана")
        return await async_run_import(hass, entries[0], dict(call.data))

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT,
        handle_import,
        schema=SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GasInvoicesConfigEntry) -> bool:
    folder = Path(entry_options(entry)[CONF_FOLDER])
    await hass.async_add_executor_job(lambda: folder.mkdir(parents=True, exist_ok=True))

    store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
    cached = await store.async_load() or {}
    entry.runtime_data = GasInvoicesData(store=store, last_result=cached.get("last_result"))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if entry_options(entry)[CONF_DAILY]:
        async def _daily(_now) -> None:
            try:
                await async_run_import(hass, entry)
            except HomeAssistantError as err:
                _LOGGER.warning("Нощният импорт не успя: %s", err)

        entry.async_on_unload(
            async_track_time_change(hass, _daily, hour=DAILY_AT[0], minute=DAILY_AT[1], second=0)
        )

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GasInvoicesConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
