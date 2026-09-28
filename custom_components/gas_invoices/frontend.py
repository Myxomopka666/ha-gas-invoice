"""Регистриране на Lovelace картата gas-invoices-card."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.start import async_at_started

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_URL_BASE = f"/{DOMAIN}"
CARD_FILE = "gas-invoices-card.js"


async def async_setup_card(hass: HomeAssistant, version: str) -> None:
    """Сервира JS файла и го регистрира:
    1) като Lovelace ресурс (dashboards в storage режим - най-надеждно);
    2) като extra module (резерва, напр. при dashboards в YAML режим)."""
    url = f"{CARD_URL_BASE}/{CARD_FILE}?v={version}"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_BASE, str(Path(__file__).parent / "www"), False)]
    )
    add_extra_js_url(hass, url)

    async def _register(_hass: HomeAssistant) -> None:
        try:
            await _async_register_resource(hass, url)
        except Exception:  # noqa: BLE001 - картата е удобство, не бива да чупи интеграцията
            _LOGGER.warning(
                "Не успях да добавя картата в Lovelace ресурсите. Добави ръчно: %s (JavaScript module)",
                url,
                exc_info=True,
            )

    async_at_started(hass, _register)


async def _async_register_resource(hass: HomeAssistant, url: str) -> None:
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        ll = hass.data.get(LOVELACE_DATA)
    except ImportError:  # по-стари версии
        ll = hass.data.get("lovelace")

    if isinstance(ll, dict):  # по-стари версии пазят речник
        mode, resources = ll.get("resource_mode", ll.get("mode")), ll.get("resources")
    else:
        mode, resources = getattr(ll, "resource_mode", None), getattr(ll, "resources", None)
    if resources is None or mode != "storage":
        _LOGGER.info(
            "Lovelace ресурсите са в YAML режим - добави ръчно: url: %s, type: module", url
        )
        return

    if not getattr(resources, "loaded", True):
        await resources.async_load()
        resources.loaded = True

    base = url.split("?")[0]
    for item in resources.async_items():
        if str(item.get("url", "")).split("?")[0] == base:
            if item["url"] != url:
                await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
                _LOGGER.info("Обновен Lovelace ресурс: %s", url)
            return

    await resources.async_create_item({"res_type": "module", "url": url})
    _LOGGER.info("Добавен Lovelace ресурс: %s", url)
