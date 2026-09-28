# Gas Invoices for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

Track your natural gas consumption and cost in the **Energy dashboard** without a gas meter sensor, using the PDF invoices from your gas supplier.

Upload your invoices (one PDF or a ZIP with many). The integration reads each invoice's billing period, meter readings, volume (m³), calorific value and amount due. It then back-fills Home Assistant's long-term statistics **for the actual days of the billing period**, not the day the invoice arrived.

> **Supported suppliers:** КОСТИНБРОДГАЗ ООД (Kostinbrod, Bulgaria). This covers all invoice layouts since 2021: BGN only, BGN with 2022 government compensation, dual EUR/BGN, and EUR only. Parsers for other suppliers are welcome, see [Contributing](#contributing).

## Features

- **Dashboard card with drag & drop:** drop many PDFs (or ZIPs) at once, see the summary of the last invoice.
- **Upload from the integration options:** a PDF or a ZIP with many PDFs. Files are validated and stored as `YYYY-MM_<invoice>.pdf`.
- **Clear upload report:** new / already uploaded / duplicates / skipped (duplicates are detected by invoice number, so the same invoice is never counted twice).
- **Three external statistics** for the Energy dashboard:
  - `gas_invoices:consumption` (m³);
  - `gas_invoices:energy` (kWh, from the calorific value on the invoice);
  - `gas_invoices:cost` (EUR; BGN invoices are converted at the fixed rate 1.95583).
- **Realistic daily profile:** the base load (hot water, cooking) is spread evenly. Heating is spread by heating degree-hours from [Open-Meteo](https://open-meteo.com/) historical temperatures at your Home Assistant location.
- **Gap filling:** if invoices are missing, consumption is taken from the meter readings of the neighbouring invoices. The cost for that period is estimated.
- Handles **meter replacements** within a billing period and **duplicate** invoices.
- **Sensors:** last invoice volume, energy, cost and €/m³, number of invoices, time of last import.
- **Import button**, `gas_invoices.import_invoices` action (with response) and optional **nightly import**.
- Event `gas_invoices_imported` with a summary, for automations and Node-RED.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/Myxomopka666/ha-gas-invoice`, category **Integration**.
2. Install **Gas Invoices** and restart Home Assistant.

### Manual

Copy `custom_components/gas_invoices` to `/config/custom_components/` and restart.

## Setup

1. Settings → Devices & services → **Add integration** → *Gas Invoices*.
2. Keep the default folder `/config/gas_invoices`, or point it to a network share (for example `/share/nas/...`, added under Settings → System → Storage).
3. Open the integration → **Configure** → **Upload invoices** and select a PDF or a ZIP. The import runs automatically after the upload.
4. Settings → Dashboards → **Energy** → *Gas consumption* → add **Gas consumption (invoices)** (`gas_invoices:consumption`). For the cost, choose *Use an entity tracking the total costs* → **Gas cost (invoices)** (`gas_invoices:cost`). With the Bulgarian UI language the names are *Газ консумация / Газ разход (фактури)*.

Home Assistant writes statistics in the background. With several years of data, allow 1–2 minutes before the charts fill in.

## Dashboard card

The integration adds the card to *Settings → Dashboards → Resources* automatically on startup (dashboards in storage mode). If your dashboards use YAML mode, add it yourself:

```yaml
lovelace:
  resources:
    - url: /gas_invoices/gas-invoices-card.js
      type: module
```

Edit a dashboard → **Add card** → search **Gas Invoices**, or use YAML:

```yaml
type: custom:gas-invoices-card
layout: tiles     # tiles (default) | chips | list
# title: My gas   (optional)
```

The card shows the number of invoices, totals, and the last invoice (m³, €, €/m³, period). It also has a drop zone for PDF/ZIP files and an **Import** button. Uploading requires an administrator account.

## Options

| Option | Default | Description |
|---|---|---|
| Folder | `/config/gas_invoices` | Where invoices are stored and read from |
| Heating base temperature | 18 °C | Outdoor temperature below which heating is assumed |
| Base load | `auto` | m³/day for hot water and cooking. `auto` = lowest invoice within ±6 months |
| Fill gaps | on | Fill missing periods from meter readings (cost is estimated) |
| Distribute by temperature | on | Off = even distribution across the period |
| Nightly import | on | Re-import every night at 03:15 (useful with a network share) |

## Action

```yaml
action: gas_invoices.import_invoices
data:
  reset: false   # true = clear the statistics first (after removing an invoice)
```

Every import rebuilds the whole history from all PDFs in the folder and overwrites the existing values, so it is safe to run as often as you like.

## Privacy

Invoices contain personal data, such as name, address and personal ID number. They are stored only in the configured folder, which is not exposed via `/local`. The extracted text is cached in `.storage/gas_invoices.<entry_id>`. Temperatures are fetched from Open-Meteo using your Home Assistant coordinates only. **Never attach real invoices to GitHub issues.**

## Contributing

`invoice.py` has no Home Assistant dependencies. To support another supplier, add a parser that turns the extracted PDF text into `Invoice` / `Segment` objects. Please use anonymised samples in tests.

---

## На български

Интеграцията прави следното:

- **Качване:** качваш газовите фактури с drag & drop в картата `custom:gas-invoices-card`, или като PDF/ZIP от Settings → Devices & services → Gas Invoices → **Configure** → **Качи фактури**.
- **Разпределение:** консумацията и цената се разпределят по дните от периода на фактурата, според температурите.
- **Energy таблото:** данните се появяват там като газ консумация и разход.
- **Поддържани фактури:** засега само от Костинбродгаз, всички формати от 2021 г. насам.

## License

MIT
