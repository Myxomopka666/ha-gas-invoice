"""Общ базов клас за entity-тата."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


class GasInvoicesEntity(Entity):
    _attr_has_entity_name = True

    def __init__(self, entry, key: str) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Gas Invoices",
            entry_type=DeviceEntryType.SERVICE,
        )
