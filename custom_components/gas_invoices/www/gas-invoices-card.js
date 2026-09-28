/*
 * Gas Invoices card - качване на фактури (drag & drop, много файлове) и резюме.
 * Зарежда се автоматично от интеграцията gas_invoices.
 *   type: custom:gas-invoices-card
 */
const CARD_VERSION = "1.1.1";
console.info(
  `%c GAS-INVOICES-CARD %c v${CARD_VERSION} `,
  "color:#fff;background:#1e78e6;font-weight:bold",
  "color:#1e78e6;background:#fff;font-weight:bold"
);

const BATCH_BYTES = 8 * 1024 * 1024; // HA приема до 16 MB на заявка

const TEXT = {
  bg: {
    title: "Газ фактури",
    drop: "Пусни PDF или ZIP файлове тук или натисни, за да избереш",
    uploading: "Качване…",
    importing: "Импорт…",
    import: "Импорт",
    invoices: "Фактури",
    total: "Общо",
    last: "Последна фактура",
    period: "Период",
    result: (u) =>
      `Нови: ${u.added} · вече качени: ${u.updated} · дубликати: ${u.duplicates} · пропуснати: ${u.skipped}`,
    imported: (i) => `Импорт: ${i.invoices} фактури, ${i.total_m3} m³, ${i.total_eur} €`,
    notAdmin: "Само администратор може да качва фактури.",
    noSensor: "Интеграцията Gas Invoices не е намерена.",
    warnings: "Предупреждения",
  },
  en: {
    title: "Gas invoices",
    drop: "Drop PDF or ZIP files here or click to choose",
    uploading: "Uploading…",
    importing: "Importing…",
    import: "Import",
    invoices: "Invoices",
    total: "Total",
    last: "Last invoice",
    period: "Period",
    result: (u) =>
      `New: ${u.added} · already uploaded: ${u.updated} · duplicates: ${u.duplicates} · skipped: ${u.skipped}`,
    imported: (i) => `Import: ${i.invoices} invoices, ${i.total_m3} m³, ${i.total_eur} €`,
    notAdmin: "Only an administrator can upload invoices.",
    noSensor: "Gas Invoices integration not found.",
    warnings: "Warnings",
  },
};

const fmt = (v, d = 2, lang) =>
  v === undefined || v === null || v === "" || isNaN(Number(v))
    ? "–"
    : Number(v).toLocaleString(lang, { maximumFractionDigits: d, minimumFractionDigits: 0 });

class GasInvoicesCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._status = "";
    this._result = null;
    this._busy = false;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._busy) return;
    // прерисуваме само ако са се променили сензорите на интеграцията
    const e = this._entities();
    const sig = [hass.language, ...Object.values(e).map((s) => s.entity_id + s.last_updated)].join("|");
    if (sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  getCardSize() {
    return 4;
  }

  static getStubConfig() {
    return {};
  }

  get _t() {
    return (this._hass?.language || "en").startsWith("bg") ? TEXT.bg : TEXT.en;
  }

  /* --- намиране на сензорите на интеграцията --- */
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
      else if (a.number !== undefined && u === "EUR") out.cost = s;
      else if (a.number !== undefined && u === "EUR/m³") out.price = s;
    }
    if (this._config.entity && h.states[this._config.entity]) out.invoices = h.states[this._config.entity];
    return out;
  }

  /* --- качване --- */
  async _upload(fileList) {
    const files = [...fileList].filter((f) => /\.(pdf|zip)$/i.test(f.name));
    if (!files.length || this._busy) return;
    if (!this._hass.user?.is_admin) {
      this._status = this._t.notAdmin;
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

    const total = { added: 0, updated: 0, duplicates: 0, skipped: 0, errors: [] };
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

  /* --- изглед --- */
  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const t = this._t;
    const e = this._entities();
    const inv = e.invoices?.attributes || {};
    const title = this._config.title || t.title;
    const lang = this._hass.locale?.language || this._hass.language;
    const n = (v, d) => fmt(v, d, lang);
    const date = (s) => (s ? new Date(s).toLocaleDateString(this._hass.language) : "–");

    let summary = `<div class="muted">${t.noSensor}</div>`;
    if (e.invoices) {
      summary = `
        <div class="grid">
          <div><span class="label">${t.invoices}</span><span class="value">${e.invoices.state}</span></div>
          <div><span class="label">${t.total}</span><span class="value">${n(inv.total_m3, 0)} m³ · ${n(inv.total_eur)} €</span></div>
          <div><span class="label">${t.last}</span><span class="value">${n(e.m3?.state, 0)} m³ · ${n(e.cost?.state)} € · ${n(e.price?.state, 3)} €/m³</span></div>
          <div><span class="label">${t.period}</span><span class="value">${date(e.m3?.attributes.from)} – ${date(e.m3?.attributes.to)}</span></div>
        </div>`;
    }

    let result = "";
    const r = this._result;
    if (r) {
      const lines = [];
      if (r.upload) lines.push(`<div>${t.result(r.upload)}</div>`);
      if (r.import)
        lines.push(
          `<div>${t.imported({ ...r.import, total_m3: n(r.import.total_m3, 0), total_eur: n(r.import.total_eur) })}</div>`
        );
      if (r.error) lines.push(`<div class="err">${r.error}</div>`);
      const errs = (r.upload?.errors || []).slice(0, 5).map((x) => `<li>${x}</li>`).join("");
      if (errs) lines.push(`<ul class="err">${errs}</ul>`);
      result = `<div class="result">${lines.join("")}</div>`;
    }
    const warn = (inv.warnings || []).length
      ? `<div class="warn">${t.warnings}: ${inv.warnings.slice(0, 3).join("; ")}</div>`
      : "";

    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        .head { display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; }
        .head h2 { margin:0; font-size:1.25em; font-weight:500; }
        .grid { display:grid; grid-template-columns: 1fr 1fr; gap:10px 16px; margin-bottom:14px; }
        .label { display:block; font-size:.8em; color: var(--secondary-text-color); }
        .value { font-weight:500; }
        .drop { border:2px dashed var(--divider-color); border-radius:12px; padding:18px; text-align:center;
                cursor:pointer; color: var(--secondary-text-color); transition: all .15s; }
        .drop:hover, .drop.over { border-color: var(--primary-color); color: var(--primary-color);
                background: color-mix(in srgb, var(--primary-color) 6%, transparent); }
        .drop.busy { opacity:.6; pointer-events:none; }
        .drop ha-icon { --mdc-icon-size: 32px; display:block; margin: 0 auto 6px; }
        .result { margin-top:12px; font-size:.9em; line-height:1.5; }
        .err { color: var(--error-color); }
        .warn { margin-top:10px; font-size:.85em; color: var(--warning-color); }
        .muted { color: var(--secondary-text-color); margin-bottom:12px; }
        ul { margin:4px 0 0; padding-left:18px; }
        button { background:none; border:1px solid var(--divider-color); border-radius:8px; padding:6px 12px;
                 color: var(--primary-text-color); cursor:pointer; display:flex; align-items:center; gap:6px; font:inherit; }
        button:hover { border-color: var(--primary-color); color: var(--primary-color); }
        button[disabled] { opacity:.5; pointer-events:none; }
        @media (max-width: 450px) { .grid { grid-template-columns: 1fr; } }
      </style>
      <ha-card>
        <div class="head">
          <h2>${title}</h2>
          <button id="imp" ${this._busy ? "disabled" : ""}><ha-icon icon="mdi:refresh"></ha-icon>${t.import}</button>
        </div>
        ${summary}
        <div class="drop ${this._busy ? "busy" : ""}" id="drop">
          <ha-icon icon="mdi:file-upload-outline"></ha-icon>
          <div>${this._status || t.drop}</div>
          <input type="file" id="file" accept=".pdf,.zip,application/pdf,application/zip" multiple hidden>
        </div>
        ${result}
        ${warn}
      </ha-card>`;

    const root = this.shadowRoot;
    const drop = root.getElementById("drop");
    const input = root.getElementById("file");
    drop.addEventListener("click", () => input.click());
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
  }
}

if (!customElements.get("gas-invoices-card")) {
  customElements.define("gas-invoices-card", GasInvoicesCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "gas-invoices-card",
    name: "Gas Invoices",
    description: "Upload gas invoices (PDF/ZIP) and show a summary",
    preview: true,
  });
}
