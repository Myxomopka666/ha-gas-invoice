"""Import of invoices into Home Assistant statistics and file uploading."""
from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics

try:  # HA 2025.x+
    from homeassistant.components.recorder.models import StatisticMeanType
except ImportError:  # older versions
    StatisticMeanType = None  # type: ignore[assignment,misc]

from . import invoice as inv_mod
from .const import (
    CONF_BASE_LOAD,
    CONF_BASE_TEMP,
    CONF_FILL_GAPS,
    CONF_FOLDER,
    CONF_RESET,
    CONF_WEATHER,
    DOMAIN,
    STAT_COST,
    STAT_KWH,
    STAT_M3,
)

_LOGGER = logging.getLogger(__name__)

MAX_ZIP_MEMBERS = 1000
MAX_PDF_SIZE = 20 * 1024 * 1024


# --------------------------------------------------------------------------- upload
RE_SAVED = re.compile(r"^\d{4}-\d{2}_(\d+)\.pdf$", re.I)


@dataclass
class UploadResult:
    """Upload result. The numbers are invoice numbers."""

    added: list[str] = field(default_factory=list)  # new to the folder
    updated: list[str] = field(default_factory=list)  # already uploaded
    duplicates: list[str] = field(default_factory=list)  # repeated in this upload
    errors: list[str] = field(default_factory=list)  # not invoices / bad files

    @property
    def saved(self) -> int:
        return len(self.added) + len(self.updated)

    def merge(self, other: "UploadResult") -> None:
        self.added += other.added
        self.updated += other.updated
        self.duplicates += other.duplicates
        self.errors += other.errors

    def as_dict(self) -> dict:
        return {
            "added": len(self.added),
            "updated": len(self.updated),
            "duplicates": len(self.duplicates),
            "skipped": len(self.errors),
            "errors": self.errors[:20],
        }


def existing_numbers(folder: Path) -> set[str]:
    """Invoice numbers already saved in the folder (by the name YYYY-MM_<number>.pdf)."""
    if not folder.is_dir():
        return set()
    return {m.group(1) for f in folder.iterdir() if (m := RE_SAVED.match(f.name))}


def save_files(
    items, folder: Path, tz: ZoneInfo, texts: dict[str, str], seen: set[str] | None = None
) -> UploadResult:
    """Saves PDF files (an iterable of (name, bytes)) to the folder.

    Each PDF is checked to be an invoice and saved as YYYY-MM_<number>.pdf.
    The extracted text is added to texts (the cache) so it isn't parsed again.
    seen - numbers already processed in the same upload (for counting duplicates).
    Blocking function - for the executor."""
    folder.mkdir(parents=True, exist_ok=True)
    before = existing_numbers(folder)
    seen = set() if seen is None else seen
    res = UploadResult()

    for name, data in items:
        if len(data) > MAX_PDF_SIZE:
            res.errors.append(f"{name}: file is too large")
            continue
        if not data.startswith(b"%PDF"):
            res.errors.append(f"{name}: not a PDF file")
            continue
        try:
            text = inv_mod.pdf_lines(io.BytesIO(data))
            inv = inv_mod.parse_text(text, tz, name)
        except inv_mod.UnknownSupplierError:
            res.errors.append(
                f"{name}: supplier not supported yet - see Configure → Invoice diagnostics"
            )
            continue
        except Exception as err:  # noqa: BLE001
            res.errors.append(f"{name}: not recognised as an invoice ({err})")
            continue
        if inv.number in seen:
            res.duplicates.append(inv.number)
            continue
        seen.add(inv.number)
        target = folder / f"{inv.start.astimezone(tz):%Y-%m}_{inv.number}.pdf"
        target.write_bytes(data)
        st = target.stat()
        texts[f"{target.name}|{st.st_size}|{int(st.st_mtime)}"] = text
        (res.updated if inv.number in before else res.added).append(inv.number)
    return res


def save_upload(src: Path, folder: Path, tz: ZoneInfo, texts: dict[str, str]) -> UploadResult:
    """Saves an uploaded PDF or a ZIP of PDFs. Blocking function - for the executor."""
    with src.open("rb") as f:
        is_pdf = f.read(4) == b"%PDF"
    if is_pdf or not zipfile.is_zipfile(src):
        return save_files([(src.name, src.read_bytes())], folder, tz, texts)

    with zipfile.ZipFile(src) as zf:
        members = [
            m for m in zf.infolist()
            if not m.is_dir()
            and m.filename.lower().endswith(".pdf")
            and "__MACOSX" not in m.filename
        ][:MAX_ZIP_MEMBERS]
        if not members:
            return UploadResult(errors=["the ZIP file contains no PDFs"])
        big = [m for m in members if m.file_size > MAX_PDF_SIZE]
        res = save_files(
            ((Path(m.filename).name, zf.read(m)) for m in members if m not in big),
            folder, tz, texts,
        )
        res.errors += [f"{Path(m.filename).name}: file is too large" for m in big]
        return res


# --------------------------------------------------------------------------- import
async def async_import(hass: HomeAssistant, store: Store, opts: dict) -> dict:
    """Reads all invoices, distributes them over hours and writes the statistics."""
    folder = Path(opts[CONF_FOLDER])
    if not await hass.async_add_executor_job(folder.is_dir):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="folder_missing",
            translation_placeholders={"folder": str(folder)},
        )

    tz = ZoneInfo(hass.config.time_zone)
    warnings: list[str] = []
    cache = await store.async_load() or {}
    texts: dict[str, str] = cache.setdefault("texts", {})
    temps_cache: dict[str, list] = cache.setdefault("temps", {})

    invoices = await hass.async_add_executor_job(
        inv_mod.load_invoices, folder, tz, warnings, texts
    )
    if not invoices:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="no_invoices",
            translation_placeholders={"folder": str(folder)},
        )
    currencies = sorted({i.currency for i in invoices})
    if len(currencies) > 1:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="mixed_currency",
            translation_placeholders={"currencies": inv_mod.currency_summary(invoices)},
        )
    currency = currencies[0]

    gaps = inv_mod.find_gaps(invoices, warnings)
    if opts[CONF_FILL_GAPS]:
        invoices = sorted(invoices + gaps, key=lambda i: i.start)

    temps = None
    if opts[CONF_WEATHER]:
        temps = await _async_temperatures(
            hass, invoices[0].start, invoices[-1].end, temps_cache, warnings
        )

    bl = str(opts[CONF_BASE_LOAD]).strip().lower()
    base_load = None if bl in ("", "auto") else float(bl.replace(",", "."))
    hourly = await hass.async_add_executor_job(
        inv_mod.distribute, invoices, temps, float(opts[CONF_BASE_TEMP]), base_load, warnings
    )

    if hass.config.currency != currency:
        warnings.append(
            f"Home Assistant currency is {hass.config.currency}, but the invoices are in {currency}"
        )

    if opts.get(CONF_RESET):
        get_instance(hass).async_clear_statistics([STAT_M3, STAT_KWH, STAT_COST])
    bg = (hass.config.language or "").startswith("bg")
    names = (
        ("Газ консумация (фактури)", "Газ енергия (фактури)", "Газ разход (фактури)")
        if bg
        else ("Gas consumption (invoices)", "Gas energy (invoices)", "Gas cost (invoices)")
    )
    _add_stats(hass, STAT_M3, names[0], "m³", "volume", hourly, 0)
    _add_stats(hass, STAT_KWH, names[1], "kWh", "energy", hourly, 1)
    _add_stats(hass, STAT_COST, names[2], currency, None, hourly, 2)

    real = [i for i in invoices if not i.estimated]
    result = {
        "imported_at": dt_util.utcnow().isoformat(),
        "folder": str(folder),
        "invoices": len(real),
        "hours": len(hourly),
        "from": invoices[0].start.astimezone(tz).isoformat(),
        "to": invoices[-1].end.astimezone(tz).isoformat(),
        # from the hourly values, so overlapping invoices are not counted twice
        "total_m3": round(sum(r[0] for r in hourly.values()), 1),
        "total": round(sum(r[2] for r in hourly.values()), 2),
        "currency": currency,
        "last_invoice": real[-1].as_dict(tz),
        "gaps": [g.as_dict(tz) for g in gaps],
        "gaps_filled": bool(opts[CONF_FILL_GAPS]),
        "warnings": warnings,
    }
    cache["last_result"] = result
    await store.async_save(cache)

    for w in warnings:
        _LOGGER.warning(w)
    _LOGGER.info(
        "Imported %s invoices (%s hours), %s m³, %s %s",
        result["invoices"], result["hours"], result["total_m3"], result["total"], currency,
    )
    return result


def _add_stats(hass, stat_id, name, unit, unit_class, hourly, idx) -> None:
    meta: dict = {
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": stat_id,
        "unit_of_measurement": unit,
    }
    if StatisticMeanType is not None:
        meta["mean_type"] = StatisticMeanType.NONE
    else:
        meta["has_mean"] = False
    if "unit_class" in getattr(StatisticMetaData, "__annotations__", {}):
        meta["unit_class"] = unit_class

    total = 0.0
    rows = []
    for hour in sorted(hourly):
        total += hourly[hour][idx]
        rows.append({"start": hour, "state": round(total, 4), "sum": round(total, 4)})
    async_add_external_statistics(hass, meta, rows)


# --------------------------------------------------------------------------- temperatures
async def _async_temperatures(
    hass: HomeAssistant,
    start: datetime,
    end: datetime,
    cache: dict[str, list],
    warnings: list[str],
) -> dict[datetime, float]:
    """Hourly temperatures (UTC) from Open-Meteo, cached by day: {"YYYY-MM-DD": [24]}."""

    def cached(h: datetime) -> float | None:
        day = cache.get(h.strftime("%Y-%m-%d"))
        return day[h.hour] if day and day[h.hour] is not None else None

    hours = list(inv_mod.hour_range(start, end))
    missing = [h for h in hours if cached(h) is None]
    if missing:
        session = async_get_clientsession(hass)
        lat, lon = hass.config.latitude, hass.config.longitude
        d1 = (missing[0] - timedelta(days=1)).date().isoformat()
        d2 = (missing[-1] + timedelta(days=1)).date().isoformat()
        urls = [
            f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
            f"&start_date={d1}&end_date={d2}&hourly=temperature_2m&timezone=GMT",
            # the archive lags ~5 days, the latest days come from the forecast
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            f"&past_days=92&forecast_days=1&hourly=temperature_2m&timezone=GMT",
        ]
        for url in urls:
            try:
                async with session.get(url, timeout=60) as resp:
                    resp.raise_for_status()
                    data = (await resp.json())["hourly"]
                for t, v in zip(data["time"], data["temperature_2m"]):
                    if v is None:
                        continue
                    day = cache.setdefault(t[:10], [None] * 24)
                    if day[int(t[11:13])] is None:
                        day[int(t[11:13])] = v
            except Exception as err:  # noqa: BLE001
                warnings.append(f"Open-Meteo: {err}")
            if all(cached(h) is not None for h in missing):
                break

    return {h.astimezone(timezone.utc): v for h in hours if (v := cached(h)) is not None}


# --------------------------------------------------------------------------- diagnostics
def debug_pdf(data: bytes, name: str, tz: ZoneInfo, extra_mask: list[str] | None = None) -> dict:
    """Extracts the PDF text, masks personal data and tries to recognise it.
    The result is safe to share when requesting a new supplier.
    Blocking function - for the executor."""
    from . import suppliers
    from .redact import redact

    if not data.startswith(b"%PDF"):
        return {"file": name, "error": "not a PDF file"}
    text = inv_mod.pdf_lines(io.BytesIO(data))
    red, masked = redact(text, extra_mask)
    sup = suppliers.detect(text)
    out = {
        "file": name,
        "supplier": sup.NAME if sup else None,
        "supported": sup is not None,
        "masked": masked,
        "chars": len(red),
        "text": red,
    }
    try:
        inv = inv_mod.parse_text(text, tz, name)
        out["parsed"] = inv.as_dict(tz)
    except Exception as err:  # noqa: BLE001
        out["error"] = str(err)
    return out
