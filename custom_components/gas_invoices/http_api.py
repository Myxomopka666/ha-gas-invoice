"""HTTP endpoint for uploading invoices from the Lovelace card (many files at once)."""
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp import web

from homeassistant.components.http import KEY_HASS_USER, HomeAssistantView
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers.http import KEY_HASS

from .const import CONF_FOLDER, DOMAIN
from .importer import MAX_PDF_SIZE, MAX_ZIP_MEMBERS, UploadResult, save_files

_LOGGER = logging.getLogger(__name__)


def _expand(items: list[tuple[str, bytes]]):
    """PDFs pass through directly, ZIP archives are unpacked."""
    for name, data in items:
        if data.startswith(b"%PDF"):
            yield name, data
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            yield name, data  # save_files will report it as "not a PDF"
            continue
        with zf:
            members = [
                m for m in zf.infolist()
                if not m.is_dir()
                and m.filename.lower().endswith(".pdf")
                and "__MACOSX" not in m.filename
                and m.file_size <= MAX_PDF_SIZE
            ][:MAX_ZIP_MEMBERS]
            if not members:
                yield name, b""  # will be reported as "not a PDF file"
            for m in members:
                yield Path(m.filename).name, zf.read(m)


def _save(items, folder: Path, tz: ZoneInfo, texts: dict) -> UploadResult:
    return save_files(_expand(items), folder, tz, texts)


class GasInvoicesUploadView(HomeAssistantView):
    """POST /api/gas_invoices/upload  (multipart: file=..., file=..., import=1|0)."""

    url = f"/api/{DOMAIN}/upload"
    name = f"api:{DOMAIN}:upload"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        from . import async_run_import, entry_options

        hass = request.app[KEY_HASS]
        user = request[KEY_HASS_USER]
        if not user.is_admin:
            raise Unauthorized()

        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            return self.json_message("Gas Invoices is not configured", 400)
        entry = entries[0]

        items: list[tuple[str, bytes]] = []
        do_import = True
        reader = await request.multipart()
        while (part := await reader.next()) is not None:
            if part.filename:
                items.append((part.filename, await part.read(decode=False)))
            elif part.name == "import":
                do_import = (await part.text()).strip() not in ("0", "false", "")
        if not items:
            return self.json_message("No files", 400)

        folder = Path(entry_options(entry)[CONF_FOLDER])
        tz = ZoneInfo(hass.config.time_zone)
        store = entry.runtime_data.store
        cache = await store.async_load() or {}
        texts = cache.setdefault("texts", {})
        res = await hass.async_add_executor_job(_save, items, folder, tz, texts)
        if res.saved:
            await store.async_save(cache)

        body: dict = {"upload": res.as_dict(), "import": None, "error": None}
        if res.saved and do_import:
            try:
                result = await async_run_import(hass, entry)
                body["import"] = {
                    k: result[k] for k in ("invoices", "total_m3", "total", "currency", "from", "to")
                } | {"warnings": result["warnings"][:10], "gaps": len(result["gaps"])}
            except HomeAssistantError as err:
                body["error"] = str(err)
        return self.json(body)
