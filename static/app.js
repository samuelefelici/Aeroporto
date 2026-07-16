/* ============================================================================
 * Flight Matrix — Shuttle Aeroporto → Turni Guida
 * Web app (vanilla JS). Il backend fa solo il parsing PDF; qui: matrice,
 * filtri (incl. fasce orarie), generazione corse e Finestra di Lavoro.
 * ==========================================================================*/

"use strict";

/* ─────────────── Costanti di dominio ─────────────── */
const LOC = { PZC: "Piazza Cavour", APT: "Aeroporto" };

// Regole di generazione corse (dalla specifica operativa)
const GEN = {
  travelDep: 40,        // Piazza Cavour → Aeroporto (partenze)
  beforeDep: 60,        // il bus arriva 1h prima della partenza del volo
  travelArr: 35,        // Aeroporto → Piazza Cavour (arrivi)
  offsetSchengen: 25,   // il bus parte 25' dopo l'atterraggio (Schengen)
  offsetExtra: 35,      // 35' dopo l'atterraggio (extra-Schengen)
};

// Fuorilinea deposito Ancona ↔ punto di servizio (minuti)
const FUORILINEA = {
  [LOC.PZC]: 10,   // dato dalla specifica: deposito Ancona → Piazza Cavour = 10'
  [LOC.APT]: 10,   // deposito Ancona → Aeroporto (assunzione: pari a PzCavour)
};

// Normativa EXTRAURBANO (da TransitIntel/optimizer-rules.ts — Accordo Quadro 18/05/2012)
const RULES = {
  intero:    { maxNastro: 480, maxLavoro: 480 },
  semiunico: { maxNastro: 540, maxLavoro: 540, intMin: 40,  intMax: 179 },
  spezzato:  { maxNastro: 630, maxLavoro: 630, intMin: 180 },
  rd131:     { maxGuidaContinuativa: 270, sostaMinima: 15 },
  hourlyRate: 22,
};
const TYPE_LABEL = { intero: "Intero (unico)", semiunico: "Semiunico", spezzato: "Spezzato" };

const WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const WEEKDAY_IT = { Mon: "Lunedì", Tue: "Martedì", Wed: "Mercoledì", Thu: "Giovedì", Fri: "Venerdì", Sat: "Sabato", Sun: "Domenica" };

const ARR_AD = new Set(["A", "ARR", "ARRIVAL"]);
const DEP_AD = new Set(["P", "D", "DEP", "DEPT", "DEPARTURE"]);

/* Schengen: set IATA (best-effort) — l'utente può comunque correggere per rotta.
 * Ancona (AOI) e destinazioni comuni; sconosciuti → extra-Schengen (margine +35'). */
const SCHENGEN_IATA = new Set((
  // Italia
  "AOI FCO CIA MXP LIN BGY BLQ NAP CTA PMO CAG OLB VCE TRN VRN PSA FLR BRI SUF REG " +
  "TPS PSR CUF PEG TSF BDS LMP PNL GOA TRS VBS RMI FOG AHO CRV SUF " +
  // Germania
  "FRA MUC DUS BER TXL HAM STR CGN NUE HAJ LEJ DTM FMM FKB SCN FDH FMO PAD " +
  // Francia
  "CDG ORY NCE LYS MRS TLS BOD NTE LIL BVA MPL SXB BIQ " +
  // Spagna
  "MAD BCN AGP PMI VLC SVQ ALC IBZ TFS TFN LPA ACE FUE SCQ BIO GRO REU MAH " +
  // Benelux
  "AMS EIN RTM BRU CRL LGG ANR LUX " +
  // Portogallo
  "LIS OPO FAO FNC PDL " +
  // Grecia / Cipro-EU
  "ATH SKG HER RHO CFU JTR CHQ KGS ZTH " +
  // Austria / Svizzera
  "VIE SZG INN GRZ ZRH GVA BSL BRN " +
  // Polonia / Cechia / Ungheria / Slovacchia / Slovenia
  "WAW KRK WRO GDN KTW POZ WMI PRG BRQ BUD BTS LJU " +
  // Scandinavia / Baltici / Islanda
  "CPH ARN GOT OSL BGO HEL TRF NYO MMX BLL AAL SVG TRD RIX TLL VNO KEF " +
  // Croazia / Romania / Bulgaria / Malta
  "ZAG SPU DBV PUY ZAD OTP CLJ TSR SOF VAR BOJ MLA"
).split(/\s+/).filter(Boolean));

const NONSCHENGEN_IATA = new Set((
  // UK / Irlanda
  "LHR LGW STN LTN MAN LCY SEN BHX EDI GLA BRS NCL LPL LBA BFS DUB ORK SNN " +
  // Balcani non-UE / Turchia / Est
  "TIA IST SAW AYT ADB BEG SKP OHD PRN TGD TIV SJJ KBP IEV KIV SVO DME LED MSQ " +
  // Nord Africa / Medio Oriente / Golfo
  "HRG SSH CAI RAK CMN AGA FEZ NDR TUN DJE MIR TLV DXB AUH TBS EVN"
).split(/\s+/).filter(Boolean));

const CITY_SCHENGEN = [
  [/stansted|luton|gatwick|heathrow|london|manchester|edinbur|dublin|birmingham|bristol/i, false],
  [/tirana|istanbul|antalya|belgrade|belgrado|cairo|hurghada|sharm|marrakech|casablanca|tunis|tel aviv|dubai|skopje/i, false],
  [/roma|rome|milan|napoli|naples|catania|palermo|bologna|venice|venez|torino|turin|bari|pisa|firenze|florence|cagliari/i, true],
  [/munich|monaco|frankfurt|dusseldorf|dus-|berlin|hamburg|stuttgart|cologne|colonia/i, true],
  [/paris|parigi|nice|nizza|lyon|marseille|barcelona|barcellona|madrid|valencia|malaga|palma|amsterdam|brussels|bruxelles|charleroi|lisbon|lisbona|porto|vienna|zurich|zurigo|geneva|ginevra|prague|praga|warsaw|varsavia|budapest|athens|atene|copenhagen|stockholm|oslo|helsinki/i, true],
];

/* ─────────────── Util ─────────────── */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

function parseHHMM(s) {
  if (s == null) return null;
  const m = /^(\d{1,2}):?(\d{2})$/.exec(String(s).trim());
  if (!m) return null;
  const h = +m[1], mi = +m[2];
  if (h > 23 || mi > 59) return null;
  return h * 60 + mi;
}
function fmtMin(min) {
  const m = ((Math.round(min) % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}
function fmtDur(min) {
  const m = Math.max(0, Math.round(min));
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}`;
}
function mode(arr) {
  const c = new Map();
  for (const v of arr) if (v != null) c.set(v, (c.get(v) || 0) + 1);
  let best = null, n = -1;
  for (const [v, k] of c) if (k > n) { n = k; best = v; }
  return best;
}
function toast(msg, kind = "info", ms = 2600) {
  const t = $("#toast");
  t.className = `toast ${kind}`;
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

function classifySchengen(route) {
  if (!route) return null;
  const up = route.toUpperCase();
  const codes = up.match(/\b[A-Z]{3}\b/g) || [];
  for (const c of codes) { if (NONSCHENGEN_IATA.has(c)) return false; if (SCHENGEN_IATA.has(c)) return true; }
  for (const [re, val] of CITY_SCHENGEN) if (re.test(route)) return val;
  return null; // sconosciuto
}

/* ─────────────── Stato globale ─────────────── */
const S = {
  flights: [],        // record dal backend
  weekday: null,
  adFilter: "all",
  slot: { from: 0, to: 1439 },
  selected: new Set(),   // chiavi riga: `${flight}|${route}|${dir}`
  rows: [],              // righe matrice del giorno corrente (dopo group, prima dei filtri)
  dates: [],             // colonne date del giorno corrente
  schengenByRoute: {},   // override utente arrivi
};

const rowKey = (r) => `${r.flight}|${r.route}|${r.dir}`;

/* ─────────────── Upload / parse ─────────────── */
const dropzone = $("#dropzone");
const fileInput = $("#fileInput");
["dragenter", "dragover"].forEach(ev => dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.add("drag"); }));
["dragleave", "drop"].forEach(ev => dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.remove("drag"); }));
dropzone.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) uploadPdf(f); });
fileInput.addEventListener("change", () => { if (fileInput.files[0]) uploadPdf(fileInput.files[0]); });

async function uploadPdf(file) {
  const st = $("#uploadStatus");
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
    st.className = "status err"; st.textContent = "Serve un file PDF."; return;
  }
  st.className = "status load"; st.textContent = "⏳ Parsing del PDF in corso…";
  const fd = new FormData(); fd.append("file", file);
  try {
    const res = await fetch("/api/parse", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Errore ${res.status}`);
    }
    const data = await res.json();
    if (!data.flights.length) { st.className = "status err"; st.textContent = "Nessun volo PAX riconosciuto nel PDF."; return; }
    S.flights = data.flights;
    st.className = "status ok"; st.textContent = `✔ ${data.meta.count} voli PAX su ${data.meta.days} giorni.`;
    initResults(data.meta);
  } catch (e) {
    st.className = "status err"; st.textContent = "✗ " + e.message;
  }
}

/* ─────────────── Matrice + filtri ─────────────── */
function initResults(meta) {
  // metriche
  const m = $("#metrics"); m.innerHTML = "";
  const addMetric = (k, v, cls = "") => { const d = el("div", "metric " + cls); d.append(el("div", "k", k), el("div", "v", v)); m.append(d); };
  addMetric("Voli PAX", meta.count);
  addMetric("Giorni coperti", meta.days);
  if (meta.start) {
    const d = el("div", "metric period"); d.append(el("div", "k", "Periodo"), el("div", "v", `${fmtDate(meta.start)} – ${fmtDate(meta.end)}`));
    m.append(d);
  }
  $("#topMeta").textContent = `${meta.count} voli · ${meta.days} giorni`;

  // giorni presenti
  const present = [...new Set(S.flights.map(f => f.weekday))].sort((a, b) => WEEKDAY_ORDER.indexOf(a) - WEEKDAY_ORDER.indexOf(b));
  const sel = $("#weekdaySelect"); sel.innerHTML = "";
  present.forEach(w => { const o = el("option"); o.value = w; o.textContent = WEEKDAY_IT[w] || w; sel.append(o); });
  S.weekday = present[0];
  sel.value = S.weekday;

  $("#resultsCard").classList.remove("hidden");
  bindFilters();
  rebuildDay();
}

function fmtDate(iso) { const [y, m, d] = iso.split("-"); return `${d}/${m}/${y}`; }

function dirOf(ad) { if (ARR_AD.has(ad)) return "A"; if (DEP_AD.has(ad)) return "P"; return null; }
function timeOfFlight(f) { const d = dirOf(f.ad); if (d === "A") return parseHHMM(f.eta); if (d === "P") return parseHHMM(f.etd); return null; }

/* costruisce le righe (Flight, Route, dir) del giorno con i tempi per data */
function rebuildDay() {
  const day = S.flights.filter(f => f.weekday === S.weekday);
  const dates = [...new Set(day.map(f => f.date))].sort();
  const map = new Map();
  for (const f of day) {
    const d = dirOf(f.ad); if (!d) continue;
    const t = timeOfFlight(f); if (t == null) continue;
    const key = `${f.flight}|${f.route}|${d}`;
    if (!map.has(key)) map.set(key, { flight: f.flight, route: f.route || "—", dir: d, ad: f.ad, times: {} });
    map.get(key).times[f.date] = t;
  }
  const rows = [...map.values()];
  for (const r of rows) { const vals = dates.map(d => r.times[d]).filter(v => v != null); r.rep = mode(vals); }
  rows.sort((a, b) => (a.rep ?? 9999) - (b.rep ?? 9999) || a.flight.localeCompare(b.flight));
  S.rows = rows; S.dates = dates;

  // popola filtro aeroporti
  const airports = [...new Set(rows.map(r => r.route))].sort();
  const af = $("#airportFilter"); const prev = new Set($$("#airportFilter option:checked").map(o => o.value));
  af.innerHTML = "";
  airports.forEach(a => { const o = el("option"); o.value = a; o.textContent = a; if (prev.has(a)) o.selected = true; af.append(o); });
  af.size = Math.min(6, Math.max(3, airports.length));

  renderMatrix();
}

function bindFilters() {
  $("#weekdaySelect").addEventListener("change", e => { S.weekday = e.target.value; S.selected.clear(); rebuildDay(); });
  $("#flightFilter").addEventListener("input", renderMatrix);
  $("#airportFilter").addEventListener("change", renderMatrix);
  $$("#adFilter button").forEach(b => b.addEventListener("click", () => {
    $$("#adFilter button").forEach(x => x.classList.remove("active")); b.classList.add("active"); S.adFilter = b.dataset.v; renderMatrix();
  }));
  // fasce orarie
  const applySlot = () => { S.slot = { from: parseHHMM($("#slotFrom").value) ?? 0, to: parseHHMM($("#slotTo").value) ?? 1439 }; markPreset(); renderMatrix(); };
  $("#slotFrom").addEventListener("change", applySlot);
  $("#slotTo").addEventListener("change", applySlot);
  $$("#slotPresets button").forEach(b => b.addEventListener("click", () => {
    const from = +b.dataset.from, to = +b.dataset.to;
    $("#slotFrom").value = fmtMin(from);
    $("#slotTo").value = fmtMin(to >= 1440 ? to - 1440 : to);
    applySlot();
  }));
  $("#selectAllBtn").addEventListener("click", () => { visibleRows().forEach(r => S.selected.add(rowKey(r))); renderMatrix(); });
  $("#clearSelBtn").addEventListener("click", () => { S.selected.clear(); renderMatrix(); });
  $("#genCorseBtn").addEventListener("click", openGenDialog);
}

function markPreset() {
  const from = parseHHMM($("#slotFrom").value) ?? 0, to = parseHHMM($("#slotTo").value) ?? 1439;
  $$("#slotPresets button").forEach(b => {
    const bf = +b.dataset.from, bt = +b.dataset.to >= 1440 ? +b.dataset.to - 1440 : +b.dataset.to;
    b.classList.toggle("active", bf === from && bt === to);
  });
}

function inSlot(t) {
  if (t == null) return false;
  const { from, to } = S.slot;
  return from <= to ? (t >= from && t <= to) : (t >= from || t <= to);
}

function visibleRows() {
  const fq = $("#flightFilter").value.trim().toLowerCase();
  const airports = new Set($$("#airportFilter option:checked").map(o => o.value));
  return S.rows.filter(r => {
    if (S.adFilter !== "all" && r.dir !== S.adFilter) return false;
    if (fq && !r.flight.toLowerCase().includes(fq)) return false;
    if (airports.size && !airports.has(r.route)) return false;
    if (!inSlot(r.rep)) return false;
    return true;
  });
}

function renderMatrix() {
  const rows = visibleRows();
  const table = $("#matrixTable"); table.innerHTML = "";
  const empty = $("#matrixEmpty");
  // slot info
  $("#slotInfo").textContent = `— ${rows.length} voli nella fascia`;
  markPreset();

  if (!rows.length) { empty.classList.remove("hidden"); updateSelCount(); return; }
  empty.classList.add("hidden");

  const thead = el("thead"); const htr = el("tr");
  htr.append(el("th", "", ""), el("th", "", "Volo"), el("th", "", "Aeroporto"), el("th", "", "A/D"), el("th", "", "Orario tipo"));
  S.dates.forEach(d => htr.append(el("th", "", d.slice(8) + "/" + d.slice(5, 7))));
  thead.append(htr); table.append(thead);

  const tbody = el("tbody");
  for (const r of rows) {
    const key = rowKey(r);
    const tr = el("tr"); if (S.selected.has(key)) tr.classList.add("sel");
    const tdChk = el("td");
    const chk = el("input"); chk.type = "checkbox"; chk.className = "chk"; chk.checked = S.selected.has(key);
    chk.addEventListener("click", e => { e.stopPropagation(); toggleRow(key); });
    tdChk.append(chk); tr.append(tdChk);
    tr.append(el("td", "", r.flight), el("td", "", r.route), el("td", "ad-" + r.dir, r.dir));
    tr.append(el("td", "col-rep", r.rep != null ? fmtMin(r.rep) : "—"));
    S.dates.forEach(d => {
      const t = r.times[d];
      const td = el("td", "time " + (r.dir === "A" ? "arr" : "dep"), t != null ? fmtMin(t) : "");
      tr.append(td);
    });
    tr.addEventListener("click", () => toggleRow(key));
    tbody.append(tr);
  }
  table.append(tbody);
  updateSelCount();
}

function toggleRow(key) { if (S.selected.has(key)) S.selected.delete(key); else S.selected.add(key); renderMatrix(); }

function updateSelCount() {
  const n = S.selected.size;
  $("#selCount").textContent = `${n} selezionat${n === 1 ? "o" : "i"}`;
  $("#genCorseBtn").disabled = n === 0;
  $("#genCorseBtn").textContent = `🚌 Genera corse${n ? ` (${n})` : ""}`;
}

/* ─────────────── Genera corse (dialog Schengen) ─────────────── */
function selectedRows() { return S.rows.filter(r => S.selected.has(rowKey(r))); }

function openGenDialog() {
  const rows = selectedRows().filter(r => r.rep != null);
  if (!rows.length) { toast("Nessun volo selezionato con orario valido.", "err"); return; }
  $("#genDayLabel").textContent = `${WEEKDAY_IT[S.weekday] || S.weekday} · ${rows.length} corse`;

  // rotte in arrivo → toggle Schengen
  const arrRoutes = [...new Set(rows.filter(r => r.dir === "A").map(r => r.route))].sort();
  const panel = $("#schengenPanel"); panel.innerHTML = "";
  if (!arrRoutes.length) {
    panel.append(el("p", "modal-sub", "Nessun volo in arrivo selezionato: la classificazione Schengen non è necessaria."));
  } else {
    panel.append(el("p", "modal-sub", "Verifica la classificazione delle rotte in ARRIVO (incide sull'orario di partenza del bus):"));
    for (const route of arrRoutes) {
      const guess = classifySchengen(route);
      if (!(route in S.schengenByRoute)) S.schengenByRoute[route] = guess === null ? false : guess;
      const row = el("div", "sch-row");
      row.append(el("span", "sch-route", route));
      const meta = el("span", "sch-meta", guess === null ? "sconosciuta → default extra-Schengen (verifica!)" : (guess ? "riconosciuta: Schengen" : "riconosciuta: extra-Schengen"));
      row.append(meta);
      const tag = el("span", "sch-tag " + (S.schengenByRoute[route] ? "s" : "n"), S.schengenByRoute[route] ? "+25′" : "+35′");
      const sw = el("label", "switch");
      const inp = el("input"); inp.type = "checkbox"; inp.checked = S.schengenByRoute[route];
      const track = el("span", "track");
      const lab = el("span", "", "Schengen");
      inp.addEventListener("change", () => {
        S.schengenByRoute[route] = inp.checked;
        tag.className = "sch-tag " + (inp.checked ? "s" : "n"); tag.textContent = inp.checked ? "+25′" : "+35′";
      });
      sw.append(inp, track, lab);
      row.append(tag, sw);
      panel.append(row);
    }
  }
  $("#genDialog").classList.remove("hidden");
}
$("#genCancel").addEventListener("click", () => $("#genDialog").classList.add("hidden"));
$("#genConfirm").addEventListener("click", () => { $("#genDialog").classList.add("hidden"); generateCorse(); });

function generateCorse() {
  const rows = selectedRows().filter(r => r.rep != null);
  const corse = [];
  let i = 0;
  for (const r of rows) {
    const id = "c" + (++i);
    if (r.dir === "P") {
      const end = r.rep - GEN.beforeDep;             // arrivo in aeroporto (1h prima del volo)
      const start = end - GEN.travelDep;             // partenza da Piazza Cavour
      corse.push({ id, flight: r.flight, route: r.route, dir: "P", from: LOC.PZC, to: LOC.APT, startMin: start, endMin: end, flightTime: r.rep, schengen: null });
    } else {
      const sch = !!S.schengenByRoute[r.route];
      const start = r.rep + (sch ? GEN.offsetSchengen : GEN.offsetExtra);  // partenza dall'aeroporto
      const end = start + GEN.travelArr;                                   // arrivo a Piazza Cavour
      corse.push({ id, flight: r.flight, route: r.route, dir: "A", from: LOC.APT, to: LOC.PZC, startMin: start, endMin: end, flightTime: r.rep, schengen: sch });
    }
  }
  corse.sort((a, b) => a.startMin - b.startMin);
  openWorkWindow(corse);
}

/* ════════════════════════════════════════════════════════════════════════
 * FINESTRA DI LAVORO
 * ══════════════════════════════════════════════════════════════════════ */
const WW = {
  open: false,
  corse: new Map(),   // id → corsa
  shifts: [],         // { id, corsaIds:[] }
  loose: new Set(),   // corsa id liberi
  selected: new Set(),
  pos: {},            // key → {x,y}
  dayLabel: "",
  seq: 0,
};

const CARD_W = 470, CARD_H = 26;
function fuoriFor(loc) { return FUORILINEA[loc] ?? 10; }

function openWorkWindow(corse) {
  WW.corse = new Map(corse.map(c => [c.id, c]));
  WW.shifts = []; WW.loose = new Set(corse.map(c => c.id)); WW.selected = new Set();
  WW.pos = {}; WW.seq = 0;
  WW.dayLabel = WEEKDAY_IT[S.weekday] || S.weekday;
  WW.open = true;
  // disposizione iniziale: colonna ordinata per orario
  orderLoose([...WW.loose]);
  const root = $("#workWindow"); root.classList.remove("hidden");
  buildWWChrome(root);
  renderWW();
  toast(`${corse.length} corse generate — componi i turni guida`, "ok");
}

function closeWorkWindow() { WW.open = false; $("#workWindow").classList.add("hidden"); $("#workWindow").innerHTML = ""; }

/* verifica normativa extraurbano di un insieme di corse */
function verifyTurno(ids) {
  const cs = ids.map(id => WW.corse.get(id)).filter(Boolean).sort((a, b) => a.startMin - b.startMin);
  const violations = [];
  if (!cs.length) return { type: "intero", valid: false, violations: ["turno vuoto"], nastroMin: 0, lavoroMin: 0, guidaMin: 0, guidaContMax: 0, costEuro: 0, cs };
  for (let i = 1; i < cs.length; i++) if (cs[i].startMin < cs[i - 1].endMin) violations.push(`Corse sovrapposte (${cs[i - 1].flight}·${cs[i].flight})`);

  const first = cs[0], last = cs[cs.length - 1];
  const preF = fuoriFor(first.from), postF = fuoriFor(last.to);
  const turnoStart = first.startMin - preF, turnoEnd = last.endMin + postF;
  const nastro = turnoEnd - turnoStart;

  const gaps = [];
  for (let i = 1; i < cs.length; i++) gaps.push(cs[i].startMin - cs[i - 1].endMin);
  const maxGap = gaps.length ? Math.max(...gaps) : 0;
  const bigGaps = gaps.filter(g => g >= RULES.semiunico.intMin);

  let type = "intero";
  if (maxGap >= RULES.spezzato.intMin) type = "spezzato";
  else if (maxGap >= RULES.semiunico.intMin) type = "semiunico";

  const guida = preF + postF + cs.reduce((s, c) => s + (c.endMin - c.startMin), 0);
  const lavoro = nastro - bigGaps.reduce((s, g) => s + g, 0);

  // guida continuativa (RD131): reset quando una sosta ≥ sostaMinima
  let run = preF + (cs[0].endMin - cs[0].startMin), maxCont = 0;
  for (let i = 1; i < cs.length; i++) {
    const gap = cs[i].startMin - cs[i - 1].endMin;
    if (gap >= RULES.rd131.sostaMinima) { maxCont = Math.max(maxCont, run); run = 0; }
    run += (cs[i].endMin - cs[i].startMin);
  }
  run += postF; maxCont = Math.max(maxCont, run);

  const lim = RULES[type];
  if (nastro > lim.maxNastro) violations.push(`Nastro ${fmtDur(nastro)} > ${fmtDur(lim.maxNastro)} (${type})`);
  if (lavoro > lim.maxLavoro) violations.push(`Lavoro ${fmtDur(lavoro)} > ${fmtDur(lim.maxLavoro)} (${type})`);
  if (maxCont > RULES.rd131.maxGuidaContinuativa) violations.push(`Guida continuativa ${fmtDur(maxCont)} > ${fmtDur(RULES.rd131.maxGuidaContinuativa)} (RD131)`);

  const costEuro = Math.round((guida / 60) * RULES.hourlyRate);
  return { type, valid: violations.length === 0, violations, nastroMin: nastro, lavoroMin: lavoro, guidaMin: guida, guidaContMax: maxCont, turnoStart, turnoEnd, gaps, costEuro, cs };
}

/* ── chrome (header + body) ── */
function buildWWChrome(root) {
  root.innerHTML = "";
  const header = el("div", "ww-header");
  header.innerHTML = `
    <button class="ww-btn" id="wwBack">← Torna alla matrice</button>
    <span class="ww-title">🪟 Finestra di Lavoro</span>
    <span class="ww-stat" id="wwStat"></span>
    <span id="wwCov"></span>
    <span id="wwSel"></span>
    <div class="ww-actions">
      <button class="ww-btn" id="wwOrder">↕ Ordina per orario</button>
      <button class="ww-btn" id="wwUnpackAll">📤 Spacchetta tutto</button>
      <button class="ww-btn primary" id="wwRepack">📦 Rimpacchetta</button>
    </div>`;
  root.append(header);

  const body = el("div", "ww-body");
  const side = el("div", "ww-side"); side.id = "wwSide"; side.dataset.wwsidebar = "1";
  const canvasWrap = el("div", "ww-canvas-wrap");
  const canvas = el("div", "ww-canvas"); canvas.id = "wwCanvas";
  canvasWrap.append(canvas);
  body.append(side, canvasWrap);
  root.append(body);

  $("#wwBack").addEventListener("click", requestCloseWW);
  $("#wwOrder").addEventListener("click", () => {
    const target = WW.selected.size ? [...WW.selected].filter(id => WW.loose.has(id)) : [...WW.loose];
    orderLoose(target.length ? target : [...WW.loose]); renderWW();
  });
  $("#wwUnpackAll").addEventListener("click", () => { WW.shifts.slice().forEach(s => unpackShift(s.id, false)); renderWW(); });
  $("#wwRepack").addEventListener("click", repack);

  canvas.addEventListener("mousedown", bandStart);
  window.addEventListener("keydown", wwKeydown);
}

function requestCloseWW() {
  const looseTrips = WW.loose.size;
  if (looseTrips > 0 && !confirm(`Ci sono ancora ${looseTrips} corse libere (scoperte). Uscire comunque?`)) return;
  window.removeEventListener("keydown", wwKeydown);
  closeWorkWindow();
}

function wwKeydown(e) {
  if (!WW.open) return;
  const tag = (e.target && e.target.tagName) || "";
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
  if (e.key === "Escape") { WW.selected.clear(); hideCtx(); renderWW(); }
  else if ((e.ctrlKey || e.metaKey) && (e.key === "a" || e.key === "A")) { e.preventDefault(); WW.loose.forEach(id => WW.selected.add(id)); renderWW(); }
}

/* ── layout helpers ── */
function orderLoose(ids) {
  const list = ids.map(id => WW.corse.get(id)).filter(Boolean).sort((a, b) => a.startMin - b.startMin);
  const perCol = Math.max(8, Math.floor((window.innerHeight - 200) / (CARD_H + 6)));
  list.forEach((c, i) => {
    const col = Math.floor(i / perCol), row = i % perCol;
    WW.pos["loose:" + c.id] = { x: 24 + col * (CARD_W + 20), y: 20 + row * (CARD_H + 6) };
  });
}
function freeShiftSpot(i) { return { x: 40 + (i % 3) * 540, y: 20 + Math.floor(i / 3) * 300 }; }

/* ── azioni turni ── */
function repack() {
  const ids = [...WW.selected];
  if (!ids.length) { toast("Seleziona almeno una corsa.", "err"); return; }
  const v = verifyTurno(ids);
  const overlap = v.violations.find(x => x.startsWith("Corse sovrapposte"));
  if (overlap) { toast(overlap + " — impossibile chiudere il turno.", "err"); return; }
  // rimuovi le corse da turni sorgente e da loose
  for (const s of WW.shifts) s.corsaIds = s.corsaIds.filter(id => !WW.selected.has(id));
  WW.shifts = WW.shifts.filter(s => s.corsaIds.length);
  ids.forEach(id => WW.loose.delete(id));
  // nuovo turno
  const nid = "FL" + String(++WW.seq).padStart(2, "0");
  WW.shifts.push({ id: nid, corsaIds: ids.slice() });
  WW.pos["shift:" + nid] = freeShiftSpot(WW.shifts.length - 1);
  WW.selected.clear();
  renderWW();
  toast(`Turno guida ${nid} creato · ${TYPE_LABEL[v.type]}${v.valid ? "" : " ⚠ con violazioni"}`, v.valid ? "ok" : "err");
}

function unpackShift(id, doRender = true) {
  const s = WW.shifts.find(x => x.id === id); if (!s) return;
  const p = WW.pos["shift:" + id] || { x: 40, y: 20 };
  const list = s.corsaIds.map(cid => WW.corse.get(cid)).sort((a, b) => a.startMin - b.startMin);
  list.forEach((c, i) => { WW.loose.add(c.id); WW.pos["loose:" + c.id] = { x: p.x, y: p.y + 40 + i * (CARD_H + 6) }; });
  WW.shifts = WW.shifts.filter(x => x.id !== id);
  if (doRender) renderWW();
}

function moveTrips(ids, targetShiftId) {
  const set = new Set(ids);
  for (const s of WW.shifts) s.corsaIds = s.corsaIds.filter(id => !set.has(id));
  ids.forEach(id => WW.loose.delete(id));
  if (targetShiftId) {
    const t = WW.shifts.find(s => s.id === targetShiftId);
    if (t) { t.corsaIds.push(...ids); t.corsaIds = [...new Set(t.corsaIds)]; }
  } else {
    ids.forEach(id => WW.loose.add(id));
  }
  WW.shifts = WW.shifts.filter(s => s.corsaIds.length);
  renderWW();
}

/* ── render ── */
function renderWW() {
  if (!WW.open) return;
  const total = WW.corse.size;
  const covered = total - WW.loose.size;
  $("#wwStat").textContent = `${WW.dayLabel} · ${WW.shifts.length} turni · ${WW.loose.size} corse libere · ${WW.selected.size} selezionate`;

  // coverage
  const cov = $("#wwCov");
  const ok = WW.loose.size === 0;
  cov.className = "ww-cov " + (ok ? "ok" : "bad");
  cov.innerHTML = `<span class="bar"><span style="width:${total ? (covered / total) * 100 : 100}%;background:${ok ? "var(--ok)" : "var(--bad)"}"></span></span> ${covered}/${total} coperte${ok ? " ✔" : " · " + WW.loose.size + " scoperte"}`;

  // selection preview
  const selWrap = $("#wwSel");
  if (WW.selected.size) {
    const v = verifyTurno([...WW.selected]);
    const bad = !v.valid;
    selWrap.className = "ww-sel " + (bad ? "bad" : "ok");
    selWrap.textContent = `${bad ? "⚠ " : ""}${WW.selected.size} sel · nastro ${fmtDur(v.nastroMin)} · lavoro ${fmtDur(v.lavoroMin)} · guida ${fmtDur(v.guidaMin)} → ${TYPE_LABEL[v.type]} ≈ €${v.costEuro}`;
    selWrap.title = v.violations.join(" · ") || "Selezione conforme alla normativa";
  } else { selWrap.className = "ww-sel"; selWrap.textContent = ""; selWrap.title = ""; }

  $("#wwRepack").disabled = WW.selected.size === 0;
  $("#wwRepack").textContent = `📦 Rimpacchetta${WW.selected.size ? ` (${WW.selected.size})` : ""}`;
  $("#wwUnpackAll").disabled = WW.shifts.length === 0;

  renderSidebar();
  renderCanvas();
}

function renderSidebar() {
  const side = $("#wwSide"); side.innerHTML = "";
  const head = el("div", "ww-side-head");
  head.innerHTML = `<div class="t">Turni guida</div><div class="h">riepilogo e normativa extraurbano</div>`;
  side.append(head);

  const counts = { intero: 0, semiunico: 0, spezzato: 0 };
  const verified = WW.shifts.map(s => ({ s, v: verifyTurno(s.corsaIds) }));
  verified.forEach(({ v }) => counts[v.type]++);

  const sum = el("div", "ww-side-item");
  sum.innerHTML = `<div class="lab">${WW.shifts.length} turni · ${verified.filter(x => !x.v.valid).length} con violazioni</div>
    <div class="sub">${counts.intero} interi · ${counts.semiunico} semiunici · ${counts.spezzato} spezzati</div>`;
  side.append(sum);

  verified.forEach(({ s, v }) => {
    const it = el("div", "ww-side-item");
    it.style.cursor = "pointer";
    it.innerHTML = `<div class="lab">${v.valid ? "✅" : "⚠️"} ${s.id} · <span style="color:var(--accent)">${TYPE_LABEL[v.type]}</span></div>
      <div class="sub">${fmtMin(v.turnoStart)}–${fmtMin(v.turnoEnd)} · nastro ${fmtDur(v.nastroMin)} · ${s.corsaIds.length} corse · ≈€${v.costEuro}</div>`;
    it.addEventListener("click", () => {
      const p = WW.pos["shift:" + s.id]; if (!p) return;
      $("#wwCanvas").parentElement.scrollTo({ left: Math.max(0, p.x - 60), top: Math.max(0, p.y - 40), behavior: "smooth" });
    });
    side.append(it);
  });

  const rec = el("div", "ww-side-item");
  rec.innerHTML = `<div class="sub" style="line-height:1.6">
    <b style="color:var(--text)">Limiti extraurbano</b><br>
    Intero: nastro/lavoro ≤ 8h00<br>
    Semiunico: interruz. 40′–2h59′ · ≤ 9h00<br>
    Spezzato: interruz. ≥ 3h00 · ≤ 10h30<br>
    Guida continuativa ≤ 4h30 (sosta 15′)<br>
    Fuorilinea deposito↔P.za Cavour: 10′</div>`;
  side.append(rec);
}

function renderCanvas() {
  const canvas = $("#wwCanvas");
  canvas.innerHTML = "";
  const maxY = Math.max(900, ...Object.values(WW.pos).map(p => p.y + 340));
  canvas.style.width = "3200px"; canvas.style.height = maxY + "px";

  if (!WW.shifts.length && !WW.loose.size) {
    const e = el("div", "ww-empty");
    e.innerHTML = `<p>Nessuna corsa.</p>`;
    canvas.append(e);
    return;
  }

  // turni
  WW.shifts.forEach(s => canvas.append(shiftCard(s)));
  // corse libere
  WW.loose.forEach(id => canvas.append(looseCard(WW.corse.get(id))));
}

function rowCells(c) {
  const wrap = document.createDocumentFragment();
  const line = el("span", "r-line", c.flight); wrap.append(line);
  wrap.append(el("span", "r-t", fmtMin(c.startMin)));
  wrap.append(el("span", "r-loc", c.from + (c.dir === "A" ? ` (${c.route})` : "")));
  wrap.append(el("span", "r-t", fmtMin(c.endMin)));
  wrap.append(el("span", "r-loc", c.to));
  return wrap;
}

function withGaps(cs) {
  const out = [];
  cs.forEach((c, i) => {
    if (i > 0) { const g = c.startMin - cs[i - 1].endMin; if (g >= 30) out.push({ gap: g }); }
    out.push({ c });
  });
  return out;
}

function shiftCard(s) {
  const v = verifyTurno(s.corsaIds);
  const card = el("div", "ww-shift");
  const p = WW.pos["shift:" + s.id] || { x: 40, y: 20 };
  card.style.left = p.x + "px"; card.style.top = p.y + "px";
  card.dataset.wwshift = s.id;

  const head = el("div", "ww-shift-head");
  head.append(el("span", "ww-shift-title", s.id));
  const badge = el("span", "ww-badge " + (v.valid ? "ok" : "bad"), v.valid ? "OK" : "⚠");
  head.append(badge);
  head.append(el("span", "ww-shift-sub", `${TYPE_LABEL[v.type]} · ${fmtMin(v.turnoStart)}–${fmtMin(v.turnoEnd)} · nastro ${fmtDur(v.nastroMin)} · ≈€${v.costEuro}`));
  const tools = el("div", "ww-shift-tools");
  const spk = el("button", "icon-btn spk", "Spacchetta"); spk.title = "Sciogli il turno in corse libere";
  spk.addEventListener("mousedown", e => e.stopPropagation());
  spk.addEventListener("click", () => unpackShift(s.id));
  tools.append(spk);
  head.append(tools);
  card.append(head);

  makeDraggable(head, "shift:" + s.id, card);

  const rows = el("div", "ww-rows");
  for (const item of withGaps(v.cs)) {
    if (item.gap != null) {
      const g = el("div", "ww-gap");
      g.append(el("span", "ln"), el("span", "", `interruzione ${fmtDur(item.gap)}`), el("span", "ln"));
      rows.append(g);
    } else {
      const c = item.c;
      const r = el("div", "ww-row pickable dir-" + c.dir); if (WW.selected.has(c.id)) r.classList.add("sel");
      r.append(rowCells(c));
      r.addEventListener("click", ev => rowClick(ev, c.id));
      r.addEventListener("contextmenu", ev => showCtx(ev, c));
      startRowDrag(r, c, s.id);
      rows.append(r);
    }
  }
  card.append(rows);

  if (!v.valid) { const vv = el("div", "ww-viol", "⚠ " + v.violations.join(" · ")); card.append(vv); }
  card.append(el("div", "ww-fuori", `fuorilinea: +${fuoriFor(v.cs[0].from)}′ deposito→${v.cs[0].from} · +${fuoriFor(v.cs[v.cs.length - 1].to)}′ ${v.cs[v.cs.length - 1].to}→deposito`));
  return card;
}

function looseCard(c) {
  const card = el("div", "ww-loose dir-" + c.dir);
  const p = WW.pos["loose:" + c.id] || { x: 24, y: 20 };
  card.style.left = p.x + "px"; card.style.top = p.y + "px";
  if (WW.selected.has(c.id)) card.classList.add("sel");
  card.append(rowCells(c));
  card.dataset.loose = c.id;
  card.addEventListener("click", ev => rowClick(ev, c.id));
  card.addEventListener("contextmenu", ev => showCtx(ev, c));
  makeLooseDraggable(card, c);
  return card;
}

/* ── selezione ── */
function rowClick(ev, id) {
  if (dragMoved) return;
  ev.stopPropagation();
  if (ev.ctrlKey || ev.metaKey) { if (WW.selected.has(id)) WW.selected.delete(id); else WW.selected.add(id); }
  else { WW.selected = new Set([id]); }
  renderWW();
}

/* selezione a rettangolo sul canvas */
function bandStart(e) {
  if (e.target !== $("#wwCanvas") || e.button !== 0) return;
  const canvas = $("#wwCanvas");
  const rc = canvas.getBoundingClientRect();
  const additive = e.ctrlKey || e.metaKey;
  const x0 = e.clientX - rc.left + canvas.parentElement.scrollLeft;
  const y0 = e.clientY - rc.top + canvas.parentElement.scrollTop;
  const band = el("div", "ww-band"); canvas.append(band);
  const draw = (x1, y1) => {
    band.style.left = Math.min(x0, x1) + "px"; band.style.top = Math.min(y0, y1) + "px";
    band.style.width = Math.abs(x1 - x0) + "px"; band.style.height = Math.abs(y1 - y0) + "px";
  };
  const move = ev => draw(ev.clientX - rc.left + canvas.parentElement.scrollLeft, ev.clientY - rc.top + canvas.parentElement.scrollTop);
  const up = ev => {
    window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up);
    const x1 = ev.clientX - rc.left + canvas.parentElement.scrollLeft, y1 = ev.clientY - rc.top + canvas.parentElement.scrollTop;
    band.remove();
    const xa = Math.min(x0, x1), xb = Math.max(x0, x1), ya = Math.min(y0, y1), yb = Math.max(y0, y1);
    if (xb - xa < 6 && yb - ya < 6) { if (!additive) { WW.selected.clear(); renderWW(); } return; }
    const hit = [];
    WW.loose.forEach(id => { const p = WW.pos["loose:" + id]; if (!p) return; if (p.x < xb && p.x + CARD_W > xa && p.y < yb && p.y + CARD_H > ya) hit.push(id); });
    if (!additive) WW.selected.clear();
    hit.forEach(id => WW.selected.add(id));
    renderWW();
  };
  window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
}

/* ── drag ── */
let dragMoved = false;

function makeDraggable(handle, key, cardEl) {
  handle.addEventListener("mousedown", e => {
    if (e.button !== 0) return;
    e.preventDefault();
    dragMoved = false;
    const start = WW.pos[key] || { x: 0, y: 0 };
    const sx = e.clientX, sy = e.clientY;
    const move = ev => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      if (!dragMoved && Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
      dragMoved = true;
      const nx = Math.max(0, start.x + dx), ny = Math.max(0, start.y + dy);
      WW.pos[key] = { x: nx, y: ny };
      cardEl.style.left = nx + "px"; cardEl.style.top = ny + "px";
    };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); setTimeout(() => dragMoved = false, 0); };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  });
}

/* corsa libera: multi-drag delle selezionate; drop su un turno = entra nel turno */
function makeLooseDraggable(cardEl, c) {
  cardEl.addEventListener("mousedown", e => {
    if (e.button !== 0) return;
    e.preventDefault();
    dragMoved = false;
    const sel = WW.selected.has(c.id) && WW.selected.size > 1;
    const keys = sel ? [...WW.selected].filter(id => WW.loose.has(id)).map(id => "loose:" + id) : ["loose:" + c.id];
    const starts = keys.map(k => ({ k, s: WW.pos[k] || { x: 0, y: 0 } }));
    const sx = e.clientX, sy = e.clientY;
    const move = ev => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      if (!dragMoved && Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
      dragMoved = true;
      starts.forEach(({ k, s }) => {
        const nx = Math.max(0, s.x + dx), ny = Math.max(0, s.y + dy);
        WW.pos[k] = { x: nx, y: ny };
        const node = $(`[data-loose="${k.slice(6)}"]`);
        if (node) { node.style.left = nx + "px"; node.style.top = ny + "px"; }
      });
    };
    const up = ev => {
      window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up);
      if (dragMoved) {
        const shiftId = shiftUnderPointer(ev);
        if (shiftId) {
          const ids = sel ? [...WW.selected].filter(id => WW.loose.has(id)) : [c.id];
          moveTrips(ids, shiftId);
          toast(`${ids.length} corse spostate nel turno ${shiftId}`, "ok");
        }
      }
      setTimeout(() => dragMoved = false, 0);
    };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  });
}

/* trascina una riga FUORI da un turno → diventa libera (o su un altro turno) */
function startRowDrag(rowEl, c, fromShiftId) {
  rowEl.addEventListener("mousedown", e => {
    if (e.button !== 0) return;
    dragMoved = false;
    const sx = e.clientX, sy = e.clientY;
    let hint = null;
    const move = ev => {
      if (!dragMoved && Math.abs(ev.clientX - sx) < 6 && Math.abs(ev.clientY - sy) < 6) return;
      dragMoved = true;
      if (!hint) { hint = el("div", "ww-drag-hint", "rilascia sul canvas per estrarre · su un turno per spostare"); document.body.append(hint); }
      hint.style.left = ev.clientX + 12 + "px"; hint.style.top = ev.clientY + 12 + "px";
    };
    const up = ev => {
      window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up);
      if (hint) hint.remove();
      if (dragMoved) {
        const target = shiftUnderPointer(ev);
        if (target && target !== fromShiftId) { moveTrips([c.id], target); }
        else if (!target) {
          const canvas = $("#wwCanvas"); const rc = canvas.getBoundingClientRect();
          if (ev.clientX >= rc.left && ev.clientY >= rc.top) {
            WW.pos["loose:" + c.id] = { x: Math.max(0, ev.clientX - rc.left + canvas.parentElement.scrollLeft - 40), y: Math.max(0, ev.clientY - rc.top + canvas.parentElement.scrollTop - 12) };
            moveTrips([c.id], null);
          }
        }
      }
      setTimeout(() => dragMoved = false, 0);
    };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  });
}

function shiftUnderPointer(ev) {
  const stack = document.elementsFromPoint(ev.clientX, ev.clientY);
  for (const node of stack) { const s = node.closest && node.closest("[data-wwshift]"); if (s) return s.getAttribute("data-wwshift"); }
  return null;
}

/* ── context menu ── */
function showCtx(ev, c) {
  ev.preventDefault(); ev.stopPropagation();
  hideCtx();
  const menu = el("div", "ww-ctx"); menu.id = "wwCtx";
  const dirTxt = c.dir === "A" ? "Arrivo" : "Partenza";
  menu.innerHTML = `
    <div class="t" style="color:${c.dir === "A" ? "var(--arr)" : "var(--dep)"}">Corsa ${c.flight} · ${dirTxt}</div>
    <div><span class="k">Rotta:</span> <b>${c.route}</b>${c.dir === "A" ? ` · ${c.schengen ? "Schengen (+25′)" : "extra-Schengen (+35′)"}` : ""}</div>
    <div><span class="k">Volo:</span> <b>${fmtMin(c.flightTime)}</b> ${c.dir === "A" ? "(atterraggio)" : "(decollo)"}</div>
    <div><span class="k">Da:</span> <b>${c.from}</b> ${fmtMin(c.startMin)}</div>
    <div><span class="k">A:</span> <b>${c.to}</b> ${fmtMin(c.endMin)}</div>
    <div><span class="k">Durata:</span> <b>${fmtDur(c.endMin - c.startMin)}</b></div>`;
  document.body.append(menu);
  menu.style.left = Math.min(ev.clientX, window.innerWidth - 250) + "px";
  menu.style.top = Math.min(ev.clientY, window.innerHeight - 170) + "px";
  setTimeout(() => { window.addEventListener("click", hideCtx, { once: true }); window.addEventListener("contextmenu", hideCtx, { once: true }); }, 0);
}
function hideCtx() { const m = $("#wwCtx"); if (m) m.remove(); }
