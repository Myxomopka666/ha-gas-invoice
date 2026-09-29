"""Сензори за последната фактура и последния импорт."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SIGNAL_UPDATED
from .entity import GasInvoicesEntity


@dataclass(frozen=True, kw_only=True)
class GasSensorDescription(SensorEntityDescription):
    value: Callable[[dict], Any]
    attrs: Callable[[dict], dict] | None = None
    unit: Callable[[dict], str] | None = None  # unit that depends on the data (currency)


def _last(r: dict) -> dict:
    return r.get("last_invoice") or {}


def _currency(r: dict) -> str:
    return r.get("currency") or _last(r).get("currency") or "EUR"


def _invoice_attrs(r: dict) -> dict:
    li = _last(r)
    return {"number": li.get("number"), "from": li.get("from"), "to": li.get("to")}


SENSORS = (
    GasSensorDescription(
        key="last_m3",
        device_class=SensorDeviceClass.GAS,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=0,
        value=lambda r: _last(r).get("m3"),
        attrs=_invoice_attrs,
    ),
    GasSensorDescription(
        key="last_kwh",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        value=lambda r: _last(r).get("kwh"),
        attrs=_invoice_attrs,
    ),
    GasSensorDescription(
        key="last_cost",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value=lambda r: _last(r).get("cost", _last(r).get("eur")),
        unit=_currency,
        attrs=_invoice_attrs,
    ),
    GasSensorDescription(
        key="last_price",
        suggested_display_precision=3,
        icon="mdi:cash",
        value=lambda r: _last(r).get("price_per_m3", _last(r).get("eur_per_m3")),
        unit=lambda r: f"{_currency(r)}/m³",
        attrs=_invoice_attrs,
    ),
    GasSensorDescription(
        key="invoices",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:file-document-multiple",
        value=lambda r: r.get("invoices"),
        attrs=lambda r: {
            "from": r.get("from"),
            "to": r.get("to"),
            "total_m3": r.get("total_m3"),
            "total": r.get("total", r.get("total_eur")),
            "currency": _currency(r),
            "gaps": r.get("gaps"),
            "warnings": r.get("warnings"),
            "folder": r.get("folder"),
        },
    ),
    GasSensorDescription(
        key="last_import",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda r: datetime.fromisoformat(r["imported_at"]) if r.get("imported_at") else None,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities(GasSensor(entry, d) for d in SENSORS)


class GasSensor(GasInvoicesEntity, SensorEntity):
    entity_description: GasSensorDescription

    def __init__(self, entry, description: GasSensorDescription) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATED.format(self._entry.entry_id), self._updated
            )
        )

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()

    @property
    def _result(self) -> dict:
        return self._entry.runtime_data.last_result or {}

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.unit is not None:
            return self.entity_description.unit(self._result)
        return super().native_unit_of_measurement

    @property
    def native_value(self):
        return self.entity_description.value(self._result)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.attrs is None:
            return None
        attrs = self.entity_description.attrs(self._result)
        if self.entity_description.key == "invoices":
            attrs["last_error"] = self._entry.runtime_data.last_error
        return attrs
