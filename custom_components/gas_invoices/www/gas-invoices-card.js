/*
 * Gas Invoices card - uploading invoices (drag & drop, many files) and a summary.
 * Loaded automatically by the gas_invoices integration.
 *
 *   type: custom:gas-invoices-card
 *   layout: tiles        # tiles (default) | chips | list
 *   title: Газ           # optional
 */
const CARD_VERSION = "1.4.0";
console.info(
  `%c GAS-INVOICES-CARD %c v${CARD_VERSION} `,
  "color:#fff;background:#1e78e6;font-weight:bold",
  "color:#1e78e6;background:#fff;font-weight:bold"
);

const BATCH_BYTES = 8 * 1024 * 1024; // HA accepts up to 16 MB per request
const LAYOUTS = ["tiles", "chips", "list"];

const TEXT = {
  bg: {
    title: "Газ фактури",
    drop: "Пусни PDF или ZIP файлове тук",
    dropHint: "или натисни, за да избереш",
    uploading: "Качване…",
    importing: "Импорт…",
    import: "Импорт",
    invoices: "Фактури",
    total: "Общо",
    totalCost: "Общо разход",
    last: "Последна фактура",
    price: "Цена",
    period: "Период",
    added: "Нови",
    updated: "Вече качени",
    duplicates: "Дубликати",
    skipped: "Пропуснати",
    files: "файла",
    uploadResult: "Резултат от качването",
    imported: "Импортирани",
    notAdmin: "Само администратор може да качва фактури.",
    noSensor: "Интеграцията Gas Invoices не е намерена.",
    warnings: "Предупреждения",
    close: "Затвори",
    layout: "Изглед",
    titleLabel: "Заглавие",
    tiles: "Плочки",
    chips: "Чипове",
    list: "Списък",
  },
  en: {
    title: "Gas invoices",
    drop: "Drop PDF or ZIP files here",
    dropHint: "or click to choose",
    uploading: "Uploading…",
    importing: "Importing…",
    import: "Import",
    invoices: "Invoices",
    total: "Total",
    totalCost: "Total cost",
    last: "Last invoice",
    price: "Price",
    period: "Period",
    added: "New",
    updated: "Already uploaded",
    duplicates: "Duplicates",
    skipped: "Skipped",
    files: "files",
    uploadResult: "Upload result",
    imported: "Imported",
    notAdmin: "Only an administrator can upload invoices.",
    noSensor: "Gas Invoices integration not found.",
    warnings: "Warnings",
    close: "Close",
    layout: "Layout",
    titleLabel: "Title",
    tiles: "Tiles",
    chips: "Chips",
    list: "List",
  },
};

// colour and icon for each result category
const RESULT_KINDS = [
  { key: "added", color: "var(--success-color, #43a047)", icon: "mdi:file-plus-outline" },
  { key: "updated", color: "var(--info-color, #039be5)", icon: "mdi:file-sync-outline" },
  { key: "duplicates", color: "var(--warning-color, #ffa600)", icon: "mdi:file-multiple-outline" },
  { key: "skipped", color: "var(--error-color, #db4437)", icon: "mdi:file-cancel-outline" },
];

const fmt = (v, d = 2, lang) =>
  v === undefined || v === null || v === "" || isNaN(Number(v))
    ? "–"
    : Number(v).toLocaleString(lang, { maximumFractionDigits: d, minimumFractionDigits: 0 });

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// currency code -> symbol in the user's locale (GBP -> £, EUR -> €)
const curSym = (code, lang) => {
  if (!code) return "";
  try {
    return (
      new Intl.NumberFormat(lang, { style: "currency", currency: code, currencyDisplay: "narrowSymbol" })
        .formatToParts(0)
        .find((p) => p.type === "currency")?.value || code
    );
  } catch (e) {
    return code;
  }
};

// Period end. Whole-day periods end at midnight of the day AFTER the last day,
// so a "T00:00:00" end time is shown as the previous calendar day. Done on the
// date part of the string, independent of the browser's time zone.
const periodEnd = (s) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})T00:00:00(?:\.0+)?(?:Z|[+-]\d{2}:?\d{2})?$/.exec(s);
  if (!m) return new Date(s);
  return new Date(+m[1], +m[2] - 1, +m[3] - 1, 12); // local noon, avoids DST edges
};

class GasInvoicesCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    if (this._config.layout && !LAYOUTS.includes(this._config.layout)) {
      throw new Error(`layout must be one of: ${LAYOUTS.join(", ")}`);
    }
    this._status = "";
    this._result = null;
    this._busy = false;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._busy) return;
    // re-render only if the integration's sensors changed
    const e = this._entities();
    const sig = [hass.language, ...Object.values(e).map((s) => s.entity_id + s.last_updated)].join("|");
    if (sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  getCardSize() {
    return this._config?.layout === "list" ? 6 : 5;
  }

  // Sections view (HA >= 2024.11): 12-column grid, ~56px rows. Full width by
  // default, resizable down to a compact minimum; height follows the content.
  getGridOptions() {
    const layout = this._config?.layout || "tiles";
    const opts = { columns: 12, min_columns: layout === "tiles" ? 6 : 4, rows: "auto" };
    opts.min_rows = layout === "tiles" ? 4 : layout === "chips" ? 3 : 4;
    return opts;
  }

  static getConfigElement() {
    return document.createElement("gas-invoices-card-editor");
  }

  static getStubConfig() {
    return { layout: "tiles" };
  }

  get _t() {
    return (this._hass?.language || "en").startsWith("bg") ? TEXT.bg : TEXT.en;
  }

  get _layout() {
    return this._config.layout || "tiles";
  }

  /* --- finding the integration's sensors --- */
  _entities() {
    const h = this._hass;
    if (!h) return {};
    let ids = Object.keys(h.entities || {}).filter((e) => h.entities[e].platform === "gas_invoices");
    if (!ids.length) ids = Object.keys(h.states).filter((e) => e.startsWith("sensor.") && e.includes("gas"));
    const out = {};
    for (const id of ids) {
      const s = h.states[id];
      if (!s || !id.startsWith("sensor.")) continue;
      const a = s.attributes;
      const u = a.unit_of_measurement;
      if (a.total_m3 !== undefined && a.gaps !== undefined) out.invoices = s;
      else if (a.number !== undefined && u === "m³") out.m3 = s;
      else if (a.number !== undefined && a.device_class === "monetary") out.cost = s;
      else if (a.number !== undefined && typeof u === "string" && u.endsWith("/m³")) out.price = s;
    }
    if (this._config.entity && h.states[this._config.entity]) out.invoices = h.states[this._config.entity];
    return out;
  }

  /* --- upload --- */
  async _upload(fileList) {
    const files = [...fileList].filter((f) => /\.(pdf|zip)$/i.test(f.name));
    if (!files.length || this._busy) return;
    if (!this._hass.user?.is_admin) {
      this._result = { error: this._t.notAdmin };
      return this._render();
    }
    this._busy = true;
    this._result = null;

    const batches = [];
    let cur = [], size = 0;
    for (const f of files.sort((a, b) => a.name.localeCompare(b.name))) {
      if (cur.length && size + f.size > BATCH_BYTES) {
        batches.push(cur);
        cur = [];
        size = 0;
      }
      cur.push(f);
      size += f.size;
    }
    if (cur.length) batches.push(cur);

    const total = { added: 0, updated: 0, duplicates: 0, skipped: 0, errors: [], files: files.length };
    let imp = null, error = null;
    try {
      for (let i = 0; i < batches.length; i++) {
        const last = i === batches.length - 1;
        this._status = last ? this._t.importing : `${this._t.uploading} ${i + 1}/${batches.length}`;
        this._render();
        const fd = new FormData();
        batches[i].forEach((f) => fd.append("file", f, f.name));
        fd.append("import", last ? "1" : "0");
        const resp = await this._hass.fetchWithAuth("/api/gas_invoices/upload", { method: "POST", body: fd });
        const body = await resp.json();
        if (!resp.ok) throw new Error(body.message || resp.statusText);
        for (const k of ["added", "updated", "duplicates", "skipped"]) total[k] += body.upload[k];
        total.errors.push(...body.upload.errors);
        if (body.import) imp = body.import;
        if (body.error) error = body.error;
      }
    } catch (e) {
      error = String(e.message || e);
    }
    this._busy = false;
    this._status = "";
    this._result = { upload: total, import: imp, error };
    this._render();
  }

  async _import() {
    if (this._busy) return;
    this._busy = true;
    this._status = this._t.importing;
    this._render();
    try {
      await this._hass.callService("gas_invoices", "import_invoices", {});
      this._result = null;
    } catch (e) {
      this._result = { error: String(e.message || e) };
    }
    this._busy = false;
    this._status = "";
    this._render();
  }

  /* --- view data --- */
  _data() {
    const e = this._entities();
    const inv = e.invoices?.attributes || {};
    const lang = this._hass.locale?.language || this._hass.language;
    const n = (v, d = 2) => fmt(v, d, lang);
    const sym = (c) => curSym(c, lang);
    const date = (s, end) =>
      s
        ? (end ? periodEnd(s) : new Date(s)).toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" })
        : "–";
    return {
      found: !!e.invoices,
      count: e.invoices?.state ?? "–",
      totalM3: n(inv.total_m3, 0),
      totalCost: n(inv.total ?? inv.total_eur),
      cur: sym(inv.currency || e.cost?.attributes.unit_of_measurement || "EUR"),
      lastM3: n(e.m3?.state, 0),
      lastCost: n(e.cost?.state),
      price: n(e.price?.state, 3),
      from: date(e.m3?.attributes.from),
      to: date(e.m3?.attributes.to, true),
      warnings: inv.warnings || [],
      n,
      sym,
    };
  }

  /* --- shared parts --- */
  _dropZone(compact = false) {
    const t = this._t;
    return `
      <div class="drop ${this._busy ? "busy" : ""} ${compact ? "compact" : ""}" id="drop" role="button" tabindex="0">
        <ha-icon icon="${this._busy ? "mdi:progress-upload" : "mdi:tray-arrow-up"}"></ha-icon>
        <div class="drop-text">
          <div class="drop-main">${this._status || t.drop}</div>
          ${this._status ? "" : `<div class="drop-hint">${t.dropHint}</div>`}
        </div>
        <input type="file" id="file" accept=".pdf,.zip,application/pdf,application/zip" multiple hidden>
      </div>`;
  }

  _importLine(d) {
    const r = this._result;
    if (!r?.import) return "";
    return `<div class="import-line"><ha-icon icon="mdi:check-circle"></ha-icon>
      ${this._t.imported}: <b>${r.import.invoices}</b> · <b>${d.n(r.import.total_m3, 0)} m³</b> · <b>${d.n(r.import.total)} ${d.sym(r.import.currency || "EUR")}</b></div>`;
  }

  _errors() {
    const r = this._result;
    if (!r) return "";
    const list = (r.upload?.errors || []).slice(0, 5).map((x) => `<li>${esc(x)}</li>`).join("");
    return `${r.error ? `<div class="err"><ha-icon icon="mdi:alert-circle"></ha-icon>${esc(r.error)}</div>` : ""}
      ${list ? `<ul class="err-list">${list}</ul>` : ""}`;
  }

  _warnings(d) {
    if (!d.warnings.length) return "";
    return `<div class="warn"><ha-icon icon="mdi:alert"></ha-icon>${this._t.warnings}: ${esc(d.warnings.slice(0, 3).join("; "))}</div>`;
  }

  _closeBtn() {
    return this._result ? `<button class="icon-btn" id="close" title="${this._t.close}"><ha-icon icon="mdi:close"></ha-icon></button>` : "";
  }

  /* --- result: 3 variants --- */
  _resultTiles(d) {
    const u = this._result?.upload;
    if (!u) return "";
    const t = this._t;
    const tiles = RESULT_KINDS.map(
      (k) => `
        <div class="rtile ${u[k.key] ? "" : "zero"}" style="--c:${k.color}">
          <ha-icon icon="${k.icon}"></ha-icon>
          <div class="rnum">${u[k.key]}</div>
          <div class="rlabel">${t[k.key]}</div>
        </div>`
    ).join("");
    return `
      <div class="result">
        <div class="result-head"><span>${t.uploadResult} · ${u.files} ${t.files}</span>${this._closeBtn()}</div>
        <div class="rtiles">${tiles}</div>
        ${this._importLine(d)}
      </div>`;
  }

  _resultChips(d) {
    const u = this._result?.upload;
    if (!u) return "";
    const t = this._t;
    const sum = RESULT_KINDS.reduce((a, k) => a + u[k.key], 0) || 1;
    const bar = RESULT_KINDS.filter((k) => u[k.key])
      .map((k) => `<span style="flex:${u[k.key]};background:${k.color}" title="${t[k.key]}: ${u[k.key]}"></span>`)
      .join("");
    const chips = RESULT_KINDS.map(
      (k) => `
        <span class="chip ${u[k.key] ? "" : "zero"}" style="--c:${k.color}">
          <span class="dot"></span>${t[k.key]}<b>${u[k.key]}</b>
        </span>`
    ).join("");
    return `
      <div class="result">
        <div class="result-head"><span>${t.uploadResult} · ${u.files} ${t.files}</span>${this._closeBtn()}</div>
        <div class="bar" aria-hidden="true">${bar || `<span style="flex:${sum};background:var(--divider-color)"></span>`}</div>
        <div class="chips">${chips}</div>
        ${this._importLine(d)}
      </div>`;
  }

  _resultList(d) {
    const u = this._result?.upload;
    if (!u) return "";
    const t = this._t;
    const rows = RESULT_KINDS.map(
      (k) => `
        <div class="lrow ${u[k.key] ? "" : "zero"}" style="--c:${k.color}">
          <ha-icon icon="${k.icon}"></ha-icon><span class="lname">${t[k.key]}</span><span class="lval">${u[k.key]}</span>
        </div>`
    ).join("");
    return `
      <div class="result">
        <div class="result-head"><span>${t.uploadResult} · ${u.files} ${t.files}</span>${this._closeBtn()}</div>
        ${rows}
        ${this._importLine(d)}
      </div>`;
  }

  /* --- summary: 3 variants --- */
  _summaryTiles(d) {
    const t = this._t;
    return `
      <div class="hero">
        <div class="hero-main">
          <div class="hero-label">${t.last}</div>
          <div class="hero-value">${d.lastCost}<span class="unit"> ${d.cur}</span></div>
          <div class="hero-sub">${d.lastM3} m³ · ${d.price} ${d.cur}/m³</div>
          <div class="hero-sub muted"><ha-icon icon="mdi:calendar-range"></ha-icon>${d.from} – ${d.to}</div>
        </div>
        <ha-icon class="hero-icon" icon="mdi:fire"></ha-icon>
      </div>
      <div class="stats">
        <div class="stat"><ha-icon icon="mdi:file-document-multiple-outline"></ha-icon><div><div class="sv">${d.count}</div><div class="sl">${t.invoices}</div></div></div>
        <div class="stat"><ha-icon icon="mdi:meter-gas-outline"></ha-icon><div><div class="sv">${d.totalM3} m³</div><div class="sl">${t.total}</div></div></div>
        <div class="stat"><ha-icon icon="mdi:cash-multiple"></ha-icon><div><div class="sv">${d.totalCost} ${d.cur}</div><div class="sl">${t.totalCost}</div></div></div>
      </div>`;
  }

  _summaryChips(d) {
    const t = this._t;
    return `
      <div class="grid">
        <div><span class="label">${t.invoices}</span><span class="value">${d.count}</span></div>
        <div><span class="label">${t.total}</span><span class="value">${d.totalM3} m³ · ${d.totalCost} ${d.cur}</span></div>
        <div><span class="label">${t.last}</span><span class="value">${d.lastM3} m³ · ${d.lastCost} ${d.cur} · ${d.price} ${d.cur}/m³</span></div>
        <div><span class="label">${t.period}</span><span class="value">${d.from} – ${d.to}</span></div>
      </div>`;
  }

  _summaryList(d) {
    const t = this._t;
    const row = (icon, name, val) =>
      `<div class="srow"><ha-icon icon="${icon}"></ha-icon><span class="lname">${name}</span><span class="sval">${val}</span></div>`;
    return `
      <div class="slist">
        ${row("mdi:file-document-multiple-outline", t.invoices, d.count)}
        ${row("mdi:meter-gas-outline", t.total, `${d.totalM3} m³`)}
        ${row("mdi:cash-multiple", t.totalCost, `${d.totalCost} ${d.cur}`)}
        ${row("mdi:fire", t.last, `${d.lastM3} m³ · ${d.lastCost} ${d.cur}`)}
        ${row("mdi:tag-outline", t.price, `${d.price} ${d.cur}/m³`)}
        ${row("mdi:calendar-range", t.period, `${d.from} – ${d.to}`)}
      </div>`;
  }

  /* --- view --- */
  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const t = this._t;
    const d = this._data();
    const layout = this._layout;
    const title = esc(this._config.title || t.title);

    let summary = `<div class="muted empty">${t.noSensor}</div>`;
    if (d.found) {
      summary = { tiles: this._summaryTiles, chips: this._summaryChips, list: this._summaryList }[layout].call(this, d);
    }
    const result = { tiles: this._resultTiles, chips: this._resultChips, list: this._resultList }[layout].call(this, d);

    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <ha-card class="layout-${layout}">
        <div class="head">
          <div class="title"><ha-icon icon="mdi:gas-burner"></ha-icon><h2>${title}</h2></div>
          <button id="imp" class="btn" ${this._busy ? "disabled" : ""}>
            <ha-icon icon="mdi:refresh" class="${this._busy ? "spin" : ""}"></ha-icon><span>${t.import}</span>
          </button>
        </div>
        ${summary}
        ${this._dropZone(layout !== "tiles")}
        ${result}
        ${this._errors()}
        ${this._warnings(d)}
      </ha-card>`;

    const root = this.shadowRoot;
    const drop = root.getElementById("drop");
    const input = root.getElementById("file");
    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", (ev) => (ev.key === "Enter" || ev.key === " ") && input.click());
    input.addEventListener("change", () => this._upload(input.files));
    drop.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      drop.classList.add("over");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (ev) => {
      ev.preventDefault();
      drop.classList.remove("over");
      this._upload(ev.dataTransfer.files);
    });
    root.getElementById("imp").addEventListener("click", () => this._import());
    root.getElementById("close")?.addEventListener("click", () => {
      this._result = null;
      this._render();
    });
  }
}

const STYLES = `
  :host { --gi-accent: var(--primary-color); --gi-soft: color-mix(in srgb, var(--primary-color) 8%, transparent); }
  ha-card { padding: 16px; container-type: inline-size; }
  ha-icon { --mdc-icon-size: 20px; }
  .head { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:14px; }
  .title { display:flex; align-items:center; gap:8px; min-width:0; }
  .title ha-icon { color: var(--gi-accent); }
  .title h2 { margin:0; font-size:1.2em; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .btn { display:flex; align-items:center; gap:6px; padding:6px 12px; border-radius:18px; font:inherit; font-size:.9em;
         background: var(--gi-soft); color: var(--gi-accent); border: none; cursor:pointer; }
  .btn:hover { background: color-mix(in srgb, var(--primary-color) 16%, transparent); }
  .btn[disabled] { opacity:.6; pointer-events:none; }
  .icon-btn { border:none; background:none; color: var(--secondary-text-color); cursor:pointer; padding:2px; border-radius:50%; display:flex; }
  .icon-btn:hover { color: var(--primary-text-color); background: var(--secondary-background-color); }
  .spin { animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .muted { color: var(--secondary-text-color); }
  .empty { margin-bottom:12px; }

  /* drop zone */
  .drop { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:4px; text-align:center;
          border:2px dashed var(--divider-color); border-radius:12px; padding:18px 12px; cursor:pointer;
          color: var(--secondary-text-color); transition: border-color .15s, background .15s, color .15s; outline:none; }
  .drop.compact { flex-direction:row; gap:10px; padding:10px 14px; text-align:left; justify-content:flex-start; }
  .drop ha-icon { --mdc-icon-size: 30px; color: var(--gi-accent); }
  .drop.compact ha-icon { --mdc-icon-size: 24px; }
  .drop-main { font-weight:500; color: var(--primary-text-color); }
  .drop-hint { font-size:.85em; }
  .drop:hover, .drop:focus-visible, .drop.over { border-color: var(--gi-accent); background: var(--gi-soft); }
  .drop.busy { pointer-events:none; opacity:.75; }
  .drop.busy ha-icon { animation: pulse 1s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity:.35; } }

  /* result common */
  .result { margin-top:12px; padding:12px; border-radius:12px; background: var(--secondary-background-color); }
  .result-head { display:flex; align-items:center; justify-content:space-between; font-size:.85em;
                 color: var(--secondary-text-color); margin-bottom:10px; }
  .import-line { display:flex; align-items:center; gap:6px; margin-top:10px; font-size:.9em; }
  .import-line ha-icon { color: var(--success-color, #43a047); --mdc-icon-size: 18px; }
  .zero { opacity:.45; }
  .err { display:flex; align-items:center; gap:6px; margin-top:10px; color: var(--error-color); font-size:.9em; }
  .err-list { margin:6px 0 0; padding-left:22px; color: var(--error-color); font-size:.85em; }
  .warn { display:flex; align-items:flex-start; gap:6px; margin-top:10px; font-size:.85em; color: var(--warning-color); }
  .warn ha-icon, .err ha-icon { --mdc-icon-size: 18px; flex-shrink:0; }

  /* ---------- layout: tiles ---------- */
  .hero { display:flex; justify-content:space-between; align-items:flex-start; padding:14px 16px; border-radius:14px; margin-bottom:12px;
          background: linear-gradient(135deg, color-mix(in srgb, var(--primary-color) 14%, transparent), color-mix(in srgb, var(--primary-color) 4%, transparent)); }
  .hero-label { font-size:.8em; color: var(--secondary-text-color); text-transform:uppercase; letter-spacing:.04em; }
  .hero-value { font-size:2em; font-weight:600; line-height:1.2; margin:2px 0; }
  .hero-value .unit { font-size:.55em; font-weight:500; color: var(--secondary-text-color); }
  .hero-sub { font-size:.9em; display:flex; align-items:center; gap:4px; margin-top:2px; }
  .hero-sub ha-icon { --mdc-icon-size: 16px; }
  .hero-icon { --mdc-icon-size: 44px; color: var(--gi-accent); opacity:.85; }
  .stats { display:grid; grid-template-columns: repeat(3, 1fr); gap:8px; margin-bottom:12px; }
  .stat { display:flex; align-items:center; gap:8px; padding:10px; border-radius:12px; background: var(--secondary-background-color); min-width:0; }
  .stat ha-icon { color: var(--gi-accent); flex-shrink:0; }
  .sv { font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .sl { font-size:.75em; color: var(--secondary-text-color); }
  .rtiles { display:grid; grid-template-columns: repeat(4, 1fr); gap:8px; }
  .rtile { text-align:center; padding:10px 4px; border-radius:10px; background: color-mix(in srgb, var(--c) 12%, transparent); }
  .rtile ha-icon { color: var(--c); }
  .rnum { font-size:1.6em; font-weight:700; color: var(--c); line-height:1.2; }
  .rlabel { font-size:.72em; color: var(--primary-text-color); }

  /* ---------- layout: chips ---------- */
  .grid { display:grid; grid-template-columns: 1fr 1fr; gap:10px 16px; margin-bottom:14px; }
  .label { display:block; font-size:.8em; color: var(--secondary-text-color); }
  .value { font-weight:500; }
  .bar { display:flex; height:8px; border-radius:4px; overflow:hidden; gap:2px; margin-bottom:10px; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; }
  .chip { display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:14px; font-size:.85em;
          background: color-mix(in srgb, var(--c) 14%, transparent); }
  .chip b { color: var(--c); font-size:1.1em; }
  .dot { width:8px; height:8px; border-radius:50%; background: var(--c); }

  /* ---------- layout: list ---------- */
  .slist { margin-bottom:12px; }
  .srow, .lrow { display:flex; align-items:center; gap:12px; padding:7px 0; }
  .srow + .srow { border-top: 1px solid var(--divider-color); }
  .srow ha-icon { color: var(--state-icon-color, var(--secondary-text-color)); }
  .lname { flex:1; }
  .sval { font-weight:500; text-align:right; }
  .lrow ha-icon { color: var(--c); }
  .lval { font-weight:700; min-width:2.5em; text-align:right; padding:1px 8px; border-radius:10px;
          background: color-mix(in srgb, var(--c) 14%, transparent); color: var(--c); }

  /* react to the card's own width (sections view), not the viewport */
  @container (max-width: 450px) {
    .grid { grid-template-columns: 1fr; }
    .stats { grid-template-columns: 1fr; }
    .rtiles { grid-template-columns: repeat(2, 1fr); }
  }
`;

// Visual editor: a single ha-form, so HA shows the Config / Visibility / Layout tabs.
class GasInvoicesCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  get _t() {
    return (this._hass?.language || "en").startsWith("bg") ? TEXT.bg : TEXT.en;
  }

  _update() {
    if (!this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        const config = { type: this._config.type, ...ev.detail.value };
        if (!config.title) delete config.title;
        this._config = config;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    const t = this._t;
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = [
      {
        name: "layout",
        selector: { select: { mode: "dropdown", options: LAYOUTS.map((l) => ({ value: l, label: t[l] })) } },
      },
      { name: "title", selector: { text: {} } },
    ];
    this._form.computeLabel = (s) => (s.name === "title" ? t.titleLabel : t[s.name] || s.name);
  }
}

// Registration. If the script is loaded before HA swaps window.customElements
// (scoped registry), the definition may stay in the old registry - so we
// check again after the page has loaded.
const TAG = "gas-invoices-card";
const defineCard = () => {
  if (customElements.get(TAG)) return;
  try {
    customElements.define(TAG, class extends GasInvoicesCard {});
  } catch (e) {
    /* already defined */
  }
};
defineCard();
window.addEventListener("load", defineCard);
setTimeout(defineCard, 1000);
setTimeout(defineCard, 5000);

const TAG_EDITOR = "gas-invoices-card-editor";
const defineEditor = () => {
  if (customElements.get(TAG_EDITOR)) return;
  try {
    customElements.define(TAG_EDITOR, class extends GasInvoicesCardEditor {});
  } catch (e) {
    /* already defined */
  }
};
defineEditor();
window.addEventListener("load", defineEditor);
setTimeout(defineEditor, 1000);
setTimeout(defineEditor, 5000);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === TAG)) {
  window.customCards.push({
    type: TAG,
    name: "Gas Invoices",
    description: "Upload gas invoices (PDF/ZIP) and show a summary",
    preview: true,
  });
}
