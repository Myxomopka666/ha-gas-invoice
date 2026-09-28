"""Бутон "Импорт на фактурите"."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import GasInvoicesEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ImportButton(entry)])


class ImportButton(GasInvoicesEntity, ButtonEntity):
    _attr_icon = "mdi:file-import"

    def __init__(self, entry) -> None:
        super().__init__(entry, "import")

    async def async_press(self) -> None:
        from . import async_run_import

        await async_run_import(self.hass, self._entry)
