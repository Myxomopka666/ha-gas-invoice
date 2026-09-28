"""Config flow и options flow (качване на фактури, настройки)."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import (
    CONF_BASE_LOAD,
    CONF_BASE_TEMP,
    CONF_DAILY,
    CONF_FILE,
    CONF_FILL_GAPS,
    CONF_FOLDER,
    CONF_WEATHER,
    DEFAULT_FOLDER_NAME,
    DEFAULTS,
    DOMAIN,
)
from .importer import save_upload


def _settings_schema(values: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_FOLDER, default=values[CONF_FOLDER]): selector.TextSelector(),
            vol.Required(CONF_BASE_TEMP, default=values[CONF_BASE_TEMP]): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=22, step=0.5, unit_of_measurement="°C",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_BASE_LOAD, default=str(values[CONF_BASE_LOAD])): selector.TextSelector(),
            vol.Required(CONF_FILL_GAPS, default=values[CONF_FILL_GAPS]): selector.BooleanSelector(),
            vol.Required(CONF_WEATHER, default=values[CONF_WEATHER]): selector.BooleanSelector(),
            vol.Required(CONF_DAILY, default=values[CONF_DAILY]): selector.BooleanSelector(),
        }
    )


def _validate(user_input: dict) -> dict[str, str]:
    errors = {}
    bl = str(user_input[CONF_BASE_LOAD]).strip().lower()
    if bl != "auto":
        try:
            if float(bl.replace(",", ".")) < 0:
                raise ValueError
        except ValueError:
            errors[CONF_BASE_LOAD] = "invalid_base_load"
    if not str(user_input[CONF_FOLDER]).startswith("/"):
        errors[CONF_FOLDER] = "invalid_folder"
    return errors


class GasInvoicesConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                title = "Газ фактури" if self.hass.config.language == "bg" else "Gas invoices"
                return self.async_create_entry(title=title, data=user_input)

        values = {
            **DEFAULTS,
            CONF_FOLDER: self.hass.config.path(DEFAULT_FOLDER_NAME),
            **(user_input or {}),
        }
        return self.async_show_form(
            step_id="user", data_schema=_settings_schema(values), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GasInvoicesOptionsFlow()


def _process_upload(hass: HomeAssistant, file_id: str, folder: Path, tz: ZoneInfo, texts: dict):
    with process_uploaded_file(hass, file_id) as path:
        return save_upload(path, folder, tz, texts)


class GasInvoicesOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["upload", "settings"])

    async def async_step_upload(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        from . import async_run_import, entry_options

        errors: dict[str, str] = {}
        placeholders = {"details": ""}
        if user_input is not None:
            entry = self.config_entry
            folder = Path(entry_options(entry)[CONF_FOLDER])
            tz = ZoneInfo(self.hass.config.time_zone)
            store = entry.runtime_data.store
            cache = await store.async_load() or {}
            texts = cache.setdefault("texts", {})
            added, problems = await self.hass.async_add_executor_job(
                _process_upload, self.hass, user_input[CONF_FILE], folder, tz, texts
            )
            if added:
                await store.async_save(cache)
            if not added:
                errors["base"] = "no_invoices"
                placeholders["details"] = "\n".join(problems[:10])
            else:
                try:
                    result = await async_run_import(self.hass, entry)
                    summary = (
                        f"{result['invoices']} фактури, {result['total_m3']} m³, "
                        f"{result['total_eur']} €"
                    )
                except HomeAssistantError as err:
                    summary = f"импортът не успя: {err}"
                return self.async_abort(
                    reason="uploaded",
                    description_placeholders={
                        "added": str(len(added)),
                        "skipped": str(len(problems)),
                        "summary": summary,
                        "problems": "\n".join(problems[:10]) or "-",
                    },
                )

        return self.async_show_form(
            step_id="upload",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FILE): selector.FileSelector(
                        selector.FileSelectorConfig(
                            accept=".pdf,.zip,application/pdf,application/zip"
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        from . import entry_options

        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        values = {**entry_options(self.config_entry), **(user_input or {})}
        return self.async_show_form(
            step_id="settings", data_schema=_settings_schema(values), errors=errors
        )
