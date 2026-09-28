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
    CONF_MASK,
    ISSUE_URL,
    CONF_WEATHER,
    DEFAULT_FOLDER_NAME,
    DEFAULTS,
    DOMAIN,
)
from .importer import debug_pdf, save_upload


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


def _process_debug(hass: HomeAssistant, file_id: str, tz: ZoneInfo, extra: list[str]) -> dict:
    with process_uploaded_file(hass, file_id) as path:
        return debug_pdf(path.read_bytes(), path.name, tz, extra)


class GasInvoicesOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["upload", "debug", "settings"])

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
            res = await self.hass.async_add_executor_job(
                _process_upload, self.hass, user_input[CONF_FILE], folder, tz, texts
            )
            if not res.saved:
                errors["base"] = "no_invoices"
                placeholders["details"] = "\n".join(res.errors[:10]) or (
                    f"дубликати: {len(res.duplicates)}"
                )
            else:
                await store.async_save(cache)
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
                        "added": str(len(res.added)),
                        "updated": str(len(res.updated)),
                        "duplicates": str(len(res.duplicates)),
                        "skipped": str(len(res.errors)),
                        "summary": summary,
                        "problems": "\n".join(res.errors[:10]) or "-",
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

    async def async_step_debug(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Диагностика: текст на фактура със замаскирани лични данни (за нов доставчик)."""
        if user_input is not None:
            tz = ZoneInfo(self.hass.config.time_zone)
            extra = [w.strip() for w in str(user_input.get(CONF_MASK, "")).split(",") if w.strip()]
            res = await self.hass.async_add_executor_job(
                _process_debug, self.hass, user_input[CONF_FILE], tz, extra
            )
            if res.get("parsed"):
                p = res["parsed"]
                status = (
                    f"✅ Разпозната фактура от **{res['supplier']}**: № {p['number']}, "
                    f"{p['m3']} m³, {p['eur']} €. Тя вече се поддържа - качи я от „Качи фактури“."
                )
            elif res.get("supported"):
                status = f"⚠️ Доставчикът **{res['supplier']}** се поддържа, но фактурата не се разчита: {res.get('error')}"
            else:
                status = "❓ Доставчикът още не се поддържа."
            text = res.get("text", "")
            if len(text) > 6000:
                text = text[:6000] + "\n… (съкратено)"
            return self.async_abort(
                reason="debug",
                description_placeholders={
                    "status": status,
                    "masked": str(res.get("masked", 0)),
                    "text": text or res.get("error", ""),
                    "issue_url": ISSUE_URL,
                },
            )

        return self.async_show_form(
            step_id="debug",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FILE): selector.FileSelector(
                        selector.FileSelectorConfig(accept=".pdf,application/pdf")
                    ),
                    vol.Optional(CONF_MASK, default=""): selector.TextSelector(),
                }
            ),
            description_placeholders={"issue_url": ISSUE_URL},
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
