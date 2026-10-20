/* Geoscan Fleet Planner — веб-интерфейс (Leaflet + vanilla JS, без сборки) */
"use strict";

// ================================================================ утилиты
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(+v)) ? "—" : Number(v).toLocaleString("ru-RU", { maximumFractionDigits: d });
// время показывается в часовом поясе района работ (из сценария/плана), а не в поясе браузера
let TZ_MIN = 180;
const tzShift = (ts) => new Date(ts * 1000 + TZ_MIN * 60000);
const hm = (ts) => tzShift(ts).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
const hms = (ts) => tzShift(ts).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC" });
const dm = (ts) => tzShift(ts).toLocaleDateString("ru-RU", { day: "numeric", month: "short", timeZone: "UTC" });
const tzFromIso = (iso) => { if (/z$/i.test(iso || "")) return 0; const m = /([+-])(\d{2}):(\d{2})$/.exec(iso || ""); return m ? (m[1] === "-" ? -1 : 1) * (+m[2] * 60 + +m[3]) : null; };
const tzLabel = (min) => `UTC${min >= 0 ? "+" : "−"}${String(Math.floor(Math.abs(min) / 60)).padStart(2, "0")}:${String(Math.abs(min) % 60).padStart(2, "0")}`;
const plural = (n, a, b, c) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? a : (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? b : c); };
const hmDur = (sec) => { const m = Math.round(sec / 60); return m < 60 ? `${m} мин` : `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, "0")} мин`; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const ICONS = {
  map: '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2Zm0 0v14m6-12v14"/>',
  upload: '<path d="M12 16V4m0 0-4 4m4-4 4 4M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  download: '<path d="M12 4v12m0 0-4-4m4 4 4-4M4 18v1a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  battery: '<rect x="3" y="7" width="16" height="10" rx="2"/><path d="M21 11v2M6 10v4m3-4v4"/>',
  play: '<path d="M7 5v14l12-7L7 5Z" fill="currentColor" stroke="none"/>',
  pause: '<path d="M8 5h3v14H8zM13 5h3v14h-3z" fill="currentColor" stroke="none"/>',
  compare: '<path d="M7 4v16M17 4v16M3 8h8M13 16h8"/>',
  code: '<path d="m8 8-4 4 4 4m8-8 4 4-4 4m-3-10-2 12"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
  expand: '<path d="M8 3H3v5m0-5 6 6m7-6h5v5m0-5-6 6M3 16v5h5m-5 0 6-6m12 1v5h-5m5 0-6-6"/>',
  chev: '<path d="m6 9 6 6 6-6"/>',
  chevL: '<path d="m15 6-6 6 6 6"/>',
  chevR: '<path d="m9 6 6 6-6 6"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  alert: '<path d="M12 4 2.5 20h19L12 4Z"/><path d="M12 10v4m0 3h.01"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="M7.5 12h9"/>',
  ok: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  route: '<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8.5 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5"/>',
  plane: '<path d="M2 13l9-2 3-7h2l-1 7 7 1v2l-7 1 1 5h-2l-3-5-9-2Z"/>',
  copter: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8 8l2.5 2.5m5.5-2.5-2.5 2.5M8 16l2.5-2.5m5.5 2.5-2.5-2.5"/><rect x="10" y="10" width="4" height="4" rx="1"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  eyeoff: '<path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3 3.8M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.6 9.6 0 0 0 4.4-1"/>',
  pin: '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21Z"/><circle cx="12" cy="9.5" r="2.5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3"/>',
  printer: '<path d="M7 9V3h10v6M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/><path d="M7 14h10v7H7z"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"/><path d="M14 3v5h5"/>',
  sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
  keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  site: '<path d="M4 20h16M6 20V9l6-5 6 5v11"/><path d="M10 20v-5h4v5"/>',
  sitePlus: '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21Z"/><path d="M12 6.5v6M9 9.5h6"/>',
};
const icon = (n) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[n] || ""}</svg>`;
const ico = (n) => `<span class="ico">${icon(n)}</span>`;
function paintIcons(root = document) { $$(".ico[data-i]", root).forEach((e) => { if (!e.dataset.done) { e.innerHTML = icon(e.dataset.i); e.dataset.done = 1; } }); }

const PALETTE = ["#2563eb", "#e11d48", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#c026d3", "#65a30d", "#b45309", "#4f46e5", "#0d9488", "#be123c"];
const MODEL_COLORS = { geoscan_201: "#2563eb", geoscan_gemini: "#16a34a", geoscan_801: "#ea580c" };
const PHASE_COLORS = { ground: "#cbd5e1", takeoff: "#f59e0b", transit_out: "#64748b", descent: "#a855f7", survey: "#2563eb", climb: "#a855f7", transit_back: "#94a3b8", landing: "#f97316" };
const PHASE_RU = { ground: "На земле", takeoff: "Взлет и набор", transit_out: "Перелет к участку", descent: "Снижение", survey: "Съемка", climb: "Набор эшелона", transit_back: "Возврат", landing: "Посадка" };
const DEFAULT_FLEET = { geoscan_201: 2, geoscan_gemini: 4, geoscan_801: 2 };
const SURVEY_RU = { rgb: "RGB", multispectral: "Мультиспектр", ir: "ИК", lidar: "Лидар", geophysics: "Геофизика" };
const SHORT = { geoscan_201: "201", geoscan_gemini: "Gemini", geoscan_801: "801" };
const STAGES = [["prepare", "Данные"], ["slicing", "Галсы"], ["allocation", "Доли площади"], ["bayes", "TPE-поиск"], ["dispatch", "Траектории"], ["validate", "Crash-тест"]];

const S = {
  catalog: null, scenario: null, scenarioId: null, plan: null, extraSites: [], layers: {}, markers: {}, colors: {},
  hidden: new Set(), showOtherDrones: false, showFutureRoutes: false, t: 0, t0: 0, t1: 0, playing: false, sel: null, fleet: {}, bats: {}, colorMode: "drone",
  objective: "time", airspace: "flight_plan", energy: "linear", wind: "qa", temp: "active", addSite: false, tzStart: 180, optimizer: "bayes", survey: "rgb",
  windDir: 250, speed: 300, basemap: "map", sortieFilter: "", job: null, cmp: null, routeLines: {}, futureLines: {}, progress: null, coverageShown: 0,
  timelineEvents: [], timelineGroups: [], highlightedEvent: null, timelineDrone: "", routeFocus: null,
  revision: 0, animationFrame: null, cancelRequested: false, frontDefault: true, sideDefault: true,
};
const objectiveName = (value) => ({time: "Минимум времени", flight: "Минимум налёта", balanced: "Баланс"}[value] || value);
let storageScope = "pending";
const storageKey = (k) => "gfp." + (k === "theme" || k === "onboarding.attempts" ? "" : storageScope + ".") + k;
const store = { get(k, d) { try { const v = localStorage.getItem(storageKey(k)); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(storageKey(k), JSON.stringify(v)); } catch (e) {} } };

// ================================================================ тема
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  $("#theme").innerHTML = ico(t === "dark" ? "sun" : "moon");
  setBasemap(S.basemap);
}
$("#theme").addEventListener("click", () => { const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; store.set("theme", t); applyTheme(t); });

// ================================================================ карта
const map = L.map("map", { zoomControl: false, attributionControl: false, zoomSnap: 0.25, zoomDelta: 0.5, preferCanvas: false }).setView([60.15, 30.5], 11);
function setPanelVisible(visible, remember = true) {
  $("#panel").hidden = !visible;
  $(".workspace").classList.toggle("panel-hidden", !visible);
  const button = $("#panel-toggle");
  button.setAttribute("aria-expanded", String(visible));
  button.title = visible ? "Скрыть левую панель" : "Показать левую панель";
  button.innerHTML = `${ico(visible ? "chevL" : "chevR")}${visible ? "Скрыть панель" : "Показать панель"}`;
  if (remember) store.set("panel.collapsed", !visible);
  requestAnimationFrame(() => { map.invalidateSize(); if (!S.userMoved) fitScenario(); });
}
$("#panel-toggle").addEventListener("click", () => setPanelVisible($("#panel").hidden));
L.control.attribution({ prefix: '<a href="https://leafletjs.com">Leaflet</a>' }).addTo(map);
L.control.zoom({ position: "bottomright" }).addTo(map);
L.control.scale({ imperial: false, position: "bottomright" }).addTo(map);
map.createPane("coveragePane").style.zIndex = 420;
const routePane = map.createPane("routePane");
routePane.style.zIndex = 450;
routePane.style.pointerEvents = "none";
routePane.classList.add("route-hit-pane");
const coverageR = L.svg({ pane: "coveragePane" });
const routeR = L.svg({ pane: "routePane", padding: 0.5 });
const canvasR = L.canvas({ padding: 0.5, tolerance: 7 });
const BASE = {
  light: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap", className: "tiles-light" }),
  dark: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap", className: "tiles-dark" }),
  sat: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, attribution: "© Esri" }),
  topo: L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", { maxZoom: 17, attribution: "© OpenTopoMap" }),
};
let curBase = null;
function setBasemap(v) {
  S.basemap = v;
  const layer = v === "sat" ? BASE.sat : v === "topo" ? BASE.topo : (document.documentElement.dataset.theme === "dark" ? BASE.dark : BASE.light);
  if (curBase === layer) return;
  if (curBase) map.removeLayer(curBase);
  curBase = layer.addTo(map); layer.bringToBack();
}
const OVERLAYS = [
  ["scenario", "Данные участка", "Область съёмки, запреты и препятствия", "Участок"],
  ["recs", "Площадки", "Точки взлёта и посадки", "Участок"],
  ["routes", "Пройденный путь", "Маршруты до выбранного времени", "Полёты"],
  ["future", "Будущий путь", "Оставшаяся часть маршрутов", "Полёты"],
  ["drones", "Борта", "Положение в выбранный момент", "Полёты"],
  ["coverage", "Отснято по плану", "Площадь съёмки к выбранному времени", "Результат"],
  ["uncovered", "Непокрытые участки", "Пробелы после расчёта", "Результат"],
  ["checks", "Точки проверки", "Контрольные точки плана", "Результат"],
];
function setLayer(name, layer) {
  if (S.layers[name]) map.removeLayer(S.layers[name]);
  S.layers[name] = layer;
  if (layer && !store.get("off." + name, false)) layer.addTo(map);
  renderOverlayList();
}
function dropLayer(name) { if (S.layers[name]) { map.removeLayer(S.layers[name]); delete S.layers[name]; } }
function renderOverlayList() {
  const focusedKey = document.activeElement?.matches?.("#overlay-list input[data-k]") ? document.activeElement.dataset.k : null;
  const available = OVERLAYS.filter(([k]) => S.layers[k]);
  const active = available.filter(([k]) => map.hasLayer(S.layers[k])).length;
  $("#overlay-count").textContent = `${active} из ${available.length}`;
  $("#overlay-list").innerHTML = ["Участок", "Полёты", "Результат"].map(group => {
    const rows = available.filter(([, , , g]) => g === group);
    if (!rows.length) return "";
    return `<div class="layers-group"><div class="layers-group-title">${group}</div>${rows.map(([k, t, desc]) =>
      `<label class="layers-overlay"><span class="layers-overlay-copy"><strong>${t}</strong><small>${desc}</small></span><input type="checkbox" data-k="${k}" ${map.hasLayer(S.layers[k]) ? "checked" : ""}><span class="layers-switch" aria-hidden="true"></span></label>`).join("")}</div>`;
  }).join("") || '<div class="layers-empty">Слои появятся после выбора участка или расчёта.</div>';
  if (focusedKey) $("#overlay-list").querySelector(`input[data-k="${focusedKey}"]`)?.focus();
  $$("#overlay-list input").forEach((i) => i.addEventListener("change", () => {
    if (i.dataset.k === "future") { setFutureRoutes(i.checked); return; }
    const l = S.layers[i.dataset.k];
    if (i.checked) l.addTo(map); else map.removeLayer(l);
    store.set("off." + i.dataset.k, !i.checked);
    if (i.dataset.k === "routes" && S.layers.future) {
      if (!i.checked) S.showFutureRoutes = false;
      if (i.checked && S.showFutureRoutes) S.layers.future.addTo(map); else map.removeLayer(S.layers.future);
    }
    renderOverlayList();
  }));
}
function showLayers(open) { $("#layers-pop").hidden = !open; $("#layers-box").classList.toggle("layers-open", open); $("#layers-btn").setAttribute("aria-expanded", String(open)); }
function setZoneLabelsVisible(visible, remember = true) {
  $("#map").classList.toggle("hide-zone-labels", !visible);
  $("#zone-labels").checked = visible;
  if (remember) store.set("off.zoneLabels", !visible);
}
$("#zone-labels").addEventListener("change", (e) => setZoneLabelsVisible(e.target.checked));
$("#layers-btn").addEventListener("click", (e) => { e.stopPropagation(); showLayers($("#layers-pop").hidden); });
$("#layers-close").addEventListener("click", () => showLayers(false));
document.addEventListener("click", (e) => { if (!$("#layers-box").contains(e.target)) showLayers(false); });
chips("#basemap", (v) => setBasemap(v));
chips("#colormode", (v) => { S.colorMode = v; store.set("colormode", v); applyRouteStyles(); legend(); });

function legend() {
  const it = (sw, t) => `<div class="legend-item"><span class="legend-swatch" style="${sw}"></span><span>${esc(t)}</span></div>`;
  const pattern = it("border-top:3px solid #475569;height:0", "Сплошная — съёмка") + it("border-top:2px dashed #475569;height:0", "Пунктир — перелёт");
  let routes = (S.plan?.drones || []).map(d => it(`border-top:3px solid ${S.colors[d.id] || "#64748b"};height:0`, d.id)).join("") || it("border-top:3px solid #2563eb;height:0", "Цвет борта");
  if (S.colorMode === "model") routes = Object.entries(MODEL_COLORS).map(([k, c]) => it(`border-top:3px solid ${c};height:0`, S.catalog?.drones[k]?.title || k)).join("");
  if (S.colorMode === "phase") routes = ["takeoff", "transit_out", "descent", "survey", "climb", "transit_back", "landing"].map(p => it(`border-top:3px solid ${PHASE_COLORS[p]};height:0`, PHASE_RU[p])).join("");
  $("#legend").innerHTML =
    `<div class="legend-group"><div class="legend-group-title">Участок и ограничения</div><div class="legend-grid">${
      it("background:rgba(37,99,235,.12);border:2px solid #2563eb", "Область съёмки") +
      it("background:rgba(220,38,38,.25);border:2px solid #dc2626", "Запретная зона") +
      it("border:2px dashed #ea580c", "Временная зона") +
      it("background:rgba(217,119,6,.13);border:2px dotted #d97706", "Ограничение высоты") +
      it("background:#facc15;border:2px solid #111;border-radius:50%;width:12px", "Препятствие") +
      it("background:#0f172a;border-radius:4px;width:15px", "Площадка взлёта")
    }</div></div>` +
    `<div class="legend-group"><div class="legend-group-title">Линии маршрута</div><div class="legend-grid">${pattern}</div></div>` +
    `<div class="legend-group"><div class="legend-group-title">Цвет линий · ${S.colorMode === "model" ? "по типам БВС" : S.colorMode === "phase" ? "по этапам" : "по бортам"}</div><div class="legend-grid">${routes}</div></div>` +
    `<div class="legend-group"><div class="legend-group-title">Результат и проверка</div><div class="legend-grid">${
      it("background:rgba(22,163,74,.23);border:1px solid #16a34a", "Отснято по плану") +
      it("background:rgba(15,23,42,.22);border:1px dashed #475569", "Не покрыто") +
      it("background:#f59e0b;border-radius:50%;width:12px;border:2px solid #fff", "Точка проверки") +
      it("background:#dc2626;border-radius:50%;width:12px;border:2px solid #fff", "Нарушение")
    }</div></div>`;
}

// ================================================================ контролы
function chips(sel, onChange) {
  const box = $(sel);
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    $$("button", box).forEach((x) => x.setAttribute("aria-checked", x === b ? "true" : "false"));
    onChange(b.dataset.v);
    if (box.closest("#tab-task")) invalidatePlan();
  });
}
function setChip(sel, v) { $$(sel + " button").forEach((x) => x.setAttribute("aria-checked", x.dataset.v === String(v) ? "true" : "false")); }

function toast(msg, kind = "info", ms = 4500) {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.innerHTML = `${ico(kind === "bad" ? "alert" : kind === "ok" ? "ok" : "info")}<div>${esc(msg)}</div>`;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

function stepperHTML(k, v, min, max, small) {
  return `<span class="stepper ${small ? "small" : ""}" data-k="${k}" data-min="${min}" data-max="${max}"><button type="button" data-d="-1" aria-label="Меньше">−</button><output>${v}</output><button type="button" data-d="1" aria-label="Больше">+</button></span>`;
}
function renderFleet() {
  $("#fleet").innerHTML = Object.entries(S.catalog.drones).map(([k, d]) => {
    const plane = d.type === "fixed_wing";
    const pl = Object.keys(d.payloads).map((p) => SURVEY_RU[p] || p).join(" · ");
    return `<div class="fleet-card ${S.fleet[k] ? "" : "off"}" data-model="${k}">
      <div class="fleet-ico">${ico(plane ? "plane" : "copter")}</div>
      <div><div class="fleet-name">${esc(d.title)}<span class="tag">${plane ? "самолет" : "коптер"}</span></div>
        <div class="fleet-spec">${d.endurance_min} мин · ${fmt(d.cruise_ms * 3.6, 0)} км/ч · ветер ≤ ${d.max_wind_ms} м/с · ${esc(pl)}</div>
        <div class="fleet-bat">АКБ на борт ${stepperHTML("bat:" + k, S.bats[k], 1, 20, true)}</div></div>
      ${stepperHTML("cnt:" + k, S.fleet[k], 0, 20)}</div>`;
  }).join("");
  summarize();
}
$("#fleet").addEventListener("click", (e) => {
  const b = e.target.closest(".stepper button"); if (!b) return;
  const st = b.closest(".stepper"); const [kind, k] = st.dataset.k.split(":");
  const obj = kind === "cnt" ? S.fleet : S.bats;
  obj[k] = Math.max(+st.dataset.min, Math.min(+st.dataset.max, obj[k] + +b.dataset.d));
  store.set("fleet", S.fleet); store.set("bats", S.bats);
  renderFleet();
  invalidatePlan();
});

// компас ветра
(function compass() {
  const g = $(".c-ticks");
  for (let a = 0; a < 360; a += 30) {
    const r = (a - 90) * Math.PI / 180;
    g.insertAdjacentHTML("beforeend", `<line x1="${60 + 48 * Math.cos(r)}" y1="${60 + 48 * Math.sin(r)}" x2="${60 + 53 * Math.cos(r)}" y2="${60 + 53 * Math.sin(r)}"/>`);
  }
  const el = $("#compass");
  const setFrom = (ev) => {
    const rc = el.getBoundingClientRect();
    const x = ev.clientX - rc.left - rc.width / 2, y = ev.clientY - rc.top - rc.height / 2;
    setWindDir(Math.round(((Math.atan2(x, -y) * 180 / Math.PI) + 360) % 360 / 5) * 5);
    invalidatePlan();
  };
  let drag = false;
  el.addEventListener("pointerdown", (e) => { drag = true; el.setPointerCapture(e.pointerId); setFrom(e); });
  el.addEventListener("pointermove", (e) => { if (drag) setFrom(e); });
  el.addEventListener("pointerup", () => { drag = false; });
  el.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight" || e.key === "ArrowUp") { setWindDir((S.windDir + 5) % 360); invalidatePlan(); e.preventDefault(); e.stopPropagation(); }
    if (e.key === "ArrowLeft" || e.key === "ArrowDown") { setWindDir((S.windDir + 355) % 360); invalidatePlan(); e.preventDefault(); e.stopPropagation(); }
  });
})();
const RUMB = ["С", "ССВ", "СВ", "ВСВ", "В", "ВЮВ", "ЮВ", "ЮЮВ", "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ", "З", "ЗСЗ", "СЗ", "ССЗ"];
function setWindDir(d) {
  d = Number(d) % 360; if (d < 0) d += 360;
  S.windDir = d;
  $("#c-arrow").setAttribute("transform", `rotate(${d} 60 60)`); // стрелка «куда дует»: хвост — откуда
  $("#wdir-v").textContent = `${d}° ${RUMB[Math.round(d / 22.5) % 16]}`;
  $("#compass").setAttribute("aria-valuenow", d);
  summarize();
}
$("#wind").addEventListener("input", () => { $("#wind-v").textContent = (+$("#wind").value).toFixed(1); summarize(); });
$("#front").addEventListener("input", () => { S.frontDefault = false; overlapControls(); summarize(); });
$("#side").addEventListener("input", () => { S.sideDefault = false; overlapControls(); summarize(); });
["#gsd_cm", "#deadline", "#effort", "#cloud", "#start", "#vlos_km", "#reserve_pct", "#min_sun", "#takeoff_weight"].forEach((s) => $(s).addEventListener("input", summarize));
function overlapControls() {
  const defaults = S.catalog?.survey_types?.[S.survey] || {};
  const camera = !defaults.kind || defaults.kind === "camera";
  $("#front").min = camera ? 70 : 50; $("#side").min = camera ? 50 : 0;
  if (S.frontDefault) $("#front").value = (defaults.front_overlap ?? 0.7) * 100;
  if (S.sideDefault) $("#side").value = (defaults.side_overlap ?? 0.5) * 100;
  for (const axis of ["front", "side"]) {
    const input = $("#" + axis);
    const value = fmt(+input.value, 1) + "%";
    $("#" + axis + "-v").textContent = value;
    $("#" + axis + "-auto").hidden = !S[axis + "Default"];
    $("#" + axis + "-min").textContent = fmt(+input.min, 0) + "%";
    input.setAttribute("aria-valuetext", value);
    const fill = Math.max(0, Math.min(100, (+input.value - +input.min) / (+input.max - +input.min) * 100));
    input.setAttribute("style", `--range-fill:${fill}%`);
  }
}
chips("#survey_type", (v) => { S.survey = v; overlapControls(); summarize(); });
$("#overlap-reset").onclick = () => { S.frontDefault = true; S.sideDefault = true; overlapControls(); invalidatePlan(); summarize(); toast("Перекрытия взяты из каталога"); };
chips("#energy_model", (v) => { S.energy = v; hints(); summarize(); });
chips("#wind_model", (v) => { S.wind = v; hints(); summarize(); });
chips("#airspace_mode", (v) => { S.airspace = v; hints(); summarize(); });
chips("#temp_restrictions", (v) => { S.temp = v; hints(); summarize(); });
chips("#optimizer", (v) => { S.optimizer = v; hints(); summarize(); });
function hints() {
  $("#vlos-field").hidden = S.airspace !== "no_flight_plan";
  $("#airspace-hint").textContent = S.airspace === "no_flight_plan"
    ? "ФП ИВП (Пост. №138): без плана полета — ниже 150 м от земли, в прямой видимости, днем, БВС до 30 кг. Самолет 201 исключается. Аэродромы, массовые мероприятия и населенные пункты задайте слоем запретных зон."
    : "Полеты по плану полета, вертикальный коридор разведения до ~700 м над землей (постановка QA-сессии).";
  $("#temp-hint").textContent = S.temp === "active"
    ? "Временные ограничения (Type=врем_ограничение), для которых в данных нет времени действия, считаются действующими весь период работ — консервативно."
    : "Выбирайте, только если действие ограничений проверено по NOTAM/плану ИВП: зоны без времени действия не учитываются ни планировщиком, ни проверкой.";
  $("#wind-hint").textContent = S.wind === "qa"
    ? "Как согласовано на QA-сессии: автопилот держит путевую скорость, ветер — порог выполнимости по ТТХ и выбор курса галсов; на скорость и расход АКБ не влияет."
    : "Путевая скорость по треугольнику скоростей: встречный ветер замедляет съемку и перелеты, попутный ускоряет — консервативная оценка времени и АКБ.";
  $("#opt-hint").textContent = S.optimizer === "bayes"
    ? "TPE (Optuna): непрерывные доли площади, ширина нарезки и размер ячеек; теплый старт от сетки. Лучшие варианты обоих методов проходят полное построение."
    : "Перебор долей площади между типами БВС по сетке с локальным уточнением.";
  const em = S.catalog && S.catalog.energy_model, v = (em && em.validation) || {};
  $("#energy-hint").textContent = S.energy === "calibrated" && em
    ? `Экспериментальная модель по ${v.flights} полетам другого аппарата: сохраненная ошибка ${v.mape_calibrated_pct}% против ${v.mape_linear_pct}% у линейной. Точность на Геоскан не измерена.`
    : "Расход пропорционален времени полета — как согласовано на QA-сессии.";
}

$("#objective").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  S.objective = b.dataset.v; setChip("#objective", S.objective); summarize();
  invalidatePlan();
});
$("#tab-task").addEventListener("input", (e) => { if (e.target.id !== "scenario" && e.target.id !== "file") invalidatePlan(); });
$("#tab-task").addEventListener("change", (e) => { if (e.target.id !== "scenario" && e.target.id !== "file") invalidatePlan(); });

function summarize() {
  if (!S.catalog) return;
  const n = Object.values(S.fleet).reduce((a, b) => a + b, 0);
  const types = Object.values(S.fleet).filter((x) => x > 0).length;
  $("#sum-fleet").textContent = `${n} ${plural(n, "борт", "борта", "бортов")} · ${types} ${plural(types, "тип", "типа", "типов")}`;
  const gsd = $("#gsd_cm").value;
  $("#sum-survey").textContent = `${SURVEY_RU[S.survey]}${gsd ? ` · ${fmt(+gsd)} см` : ""} · ${fmt(+$("#front").value)} / ${fmt(+$("#side").value)}%`;
  $("#sum-cond").textContent = `${S.airspace === "no_flight_plan" ? "≤150 м · " : ""}${(+$("#wind").value).toFixed(1)} м/с ${RUMB[Math.round(S.windDir / 22.5) % 16]} · ${$("#start").value.slice(5, 16).replace("T", " ")}`;
  $("#sum-opt").textContent = `${$("#deadline").value ? "срок " + $("#deadline").value + " ч · " : ""}${$("#effort").selectedOptions[0].text.toLowerCase()}${S.optimizer === "grid" ? " · сетка" : " · TPE"}${S.energy === "calibrated" ? " · АКБ: телеметрия" : ""}`;
  $("#tf-sum").innerHTML = `<b>${n} ${plural(n, "борт", "борта", "бортов")}</b> · ${SURVEY_RU[S.survey]}${gsd ? ` ${gsd} см` : ""} · ветер ${(+$("#wind").value).toFixed(1)} м/с<br>${objectiveName(S.objective)} · ${S.wind === "qa" ? "ветер по методике QA" : "физическая модель ветра"}`;
}

function params(objective) {
  const num = (id) => { const v = $(id).value; return v === "" ? null : +v; };
  const p = {
    survey_type: S.survey, gsd_cm: num("#gsd_cm"),
    front_overlap: S.frontDefault ? null : +$("#front").value / 100, side_overlap: S.sideDefault ? null : +$("#side").value / 100,
    fleet: Object.keys(S.fleet).map((k) => ({ model: k, count: S.fleet[k], batteries: S.bats[k] })),
    objective: objective || S.objective, deadline_h: num("#deadline"),
    wind_speed_ms: +$("#wind").value, wind_dir_deg: S.windDir, cloud_base_m: num("#cloud"),
    respect_daylight: $("#daylight").checked, use_terrain: $("#terrain").checked,
    reserve_pct: +$("#reserve_pct").value, min_sun_elevation_deg: +$("#min_sun").value, takeoff_weight_h: +$("#takeoff_weight").value,
    search_effort: $("#effort").value, extra_sites: S.extraSites.map(({ lon, lat, name }) => ({ lon, lat, name })), energy_model: S.energy, wind_model: S.wind,
    airspace_mode: S.airspace, vlos_radius_km: +$("#vlos_km").value || 2, optimizer: S.optimizer, temp_restrictions: S.temp,
  };
  if ($("#start").value) {
    const local = $("#start").value;
    p.start_time = local + (local.length === 16 ? ":00" : "") + tzLabel(S.tzStart).slice(3).replace("−", "-");
  }
  return p;
}
// параметры плана из истории — обратно в форму (план воспроизводим одним нажатием)
function restoreParams(p) {
  if (!p) return;
  Object.keys(S.fleet).forEach((k) => { S.fleet[k] = 0; S.bats[k] = S.catalog.drones[k].batteries; });
  (p.fleet || []).forEach((f) => { if (f.model in S.fleet) { S.fleet[f.model] = f.count; if (f.batteries) S.bats[f.model] = f.batteries; } });
  renderFleet();
  S.survey = p.survey_type || "rgb"; setChip("#survey_type", S.survey);
  S.frontDefault = p.front_overlap == null; S.sideDefault = p.side_overlap == null;
  overlapControls();
  $("#gsd_cm").value = p.gsd_cm ?? "";
  if (!S.frontDefault) $("#front").value = p.front_overlap * 100;
  if (!S.sideDefault) $("#side").value = p.side_overlap * 100;
  overlapControls();
  $("#wind").value = p.wind_speed_ms ?? 5; $("#wind-v").textContent = (+$("#wind").value).toFixed(1);
  setWindDir(p.wind_dir_deg ?? 250);
  $("#cloud").value = p.cloud_base_m ?? ""; $("#deadline").value = p.deadline_h ?? "";
  $("#daylight").checked = p.respect_daylight !== false; $("#terrain").checked = p.use_terrain !== false;
  $("#reserve_pct").value = p.reserve_pct ?? 20;
  $("#min_sun").value = p.min_sun_elevation_deg ?? 10;
  $("#takeoff_weight").value = p.takeoff_weight_h ?? 0;
  $("#effort").value = p.search_effort || "normal";
  S.objective = p.objective || "time"; setChip("#objective", S.objective);
  S.energy = p.energy_model || "linear"; setChip("#energy_model", S.energy);
  S.wind = p.wind_model || "qa"; setChip("#wind_model", S.wind);
  S.airspace = p.airspace_mode || "flight_plan"; setChip("#airspace_mode", S.airspace);
  S.optimizer = p.optimizer || "bayes"; setChip("#optimizer", S.optimizer);
  S.temp = p.temp_restrictions || "active"; setChip("#temp_restrictions", S.temp);
  $("#vlos_km").value = p.vlos_radius_km ?? 2;
  if (p.start_time) {
    const tz = tzFromIso(p.start_time); if (tz !== null) { S.tzStart = tz; $("#tz-label").textContent = tzLabel(tz); }
    $("#start").value = p.start_time.replace(/(z|[+-]\d{2}:\d{2})$/i, "");
  }
  S.extraSites = (p.extra_sites || []).map((x) => ({ lon: x.lon, lat: x.lat, name: x.name, manual: true }));
  hints(); summarize();
}

// ================================================================ сценарий
function styleScenario(f) {
  const k = f.properties.kind, p = f.properties;
  if (k === "survey_area") return { color: "#2563eb", weight: 2, fillColor: "#2563eb", fillOpacity: 0.06 };
  if (k === "no_fly") {
    if (p.active_from || p.active_to || p.category === "TEMP") return { color: "#ea580c", weight: 2, dashArray: "7 5", fillColor: "#ea580c", fillOpacity: 0.12 };
    if (p.alt_min_m != null || p.alt_max_m != null) return { color: "#d97706", weight: 1.5, dashArray: "2 6", fillColor: "#d97706", fillOpacity: 0.13 };
    return { color: "#dc2626", weight: 2, fillColor: "#dc2626", fillOpacity: 0.2 };
  }
  if (k === "obstacle") return { color: "#111827", weight: 3, dashArray: "1 6" };
  if (k === "airspace") return { color: "#16a34a", weight: 2, dashArray: "8 6", fill: false };
  return {};
}
const ZONE_RU = { P: "Запретная зона (P)", R: "Зона ограничения полетов (R)", TEMP: "Временное ограничение", SVO: "Особый режим (SVO)" };
const KIND_RU = { survey_area: "Область съемки", no_fly: "Запретная зона", obstacle: "Высотное препятствие", airspace: "Разрешенное пространство", site: "Площадка", reserve_site: "Резервная площадка" };
function popupScenario(p) {
  const rows = [];
  if (p.height_m != null) rows.push([p.alt_ref === "AMSL" ? "Отметка верха" : "Высота", `${p.height_m} м ${p.alt_ref === "AMSL" ? "над уровнем моря" : "над землей"}`]);
  if (p.alt_min_m != null) rows.push(["Высоты", `${p.alt_min_m}–${p.alt_max_m ?? "∞"} м ${p.alt_ref || "AGL"}`]);
  if (p.active_from) rows.push(["Действует", `${p.active_from.slice(11, 16)}–${(p.active_to || "").slice(11, 16)} ${p.active_from.slice(0, 10)}`]);
  if (p.kind === "no_fly" && p.alt_min_m == null && p.alt_max_m != null) rows.push(["Высоты", `от земли до ${Math.round(p.alt_max_m)} м ${p.alt_ref || "AGL"}`]);
  if (p.time_unknown) rows.push(["Время действия", S.temp === "active" ? "не указано → считается действующей" : "не указано → не учитывается"]);
  const kindT = p.kind === "no_fly" && p.category ? ZONE_RU[p.category] || KIND_RU.no_fly : KIND_RU[p.kind] || p.kind;
  return `<div class="pp-t">${esc(p.name || KIND_RU[p.kind])}</div><div class="hint">${esc(kindT)}</div>` +
    rows.map(([a, b]) => `<div class="pp-row"><span>${a}</span><span>${esc(b)}</span></div>`).join("") +
    (p.note ? `<div class="hint" style="margin-top:6px;max-width:260px;white-space:pre-line">${esc(p.note)}</div>` : "");
}
function siteIcon(kind) {
  const bg = kind === "reserve" ? "#64748b" : kind === "extra" ? "#2563eb" : "#0f172a";
  return L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13], html: `<div class="site-ico" style="background:${bg}">${kind === "reserve" ? "R" : "H"}</div>` });
}
function zoneLabel(p) {
  const temporary = !!(p.active_from || p.active_to || p.category === "TEMP");
  const height = !temporary && (p.alt_min_m != null || p.alt_max_m != null);
  const radius = /\bR\s*=\s*(\d+(?:[.,]\d+)?)\s*(км|м)(?=\s|[)]|$)/i.exec(p.name || "");
  const detail = height
    ? p.alt_min_m != null ? `от ${fmt(p.alt_min_m, 0)} м` : `до ${fmt(p.alt_max_m, 0)} м`
    : radius ? `R = ${radius[1]} ${radius[2]}`
    : temporary && p.active_from && p.active_to ? `${p.active_from.slice(11, 16)}–${p.active_to.slice(11, 16)}`
    : (p.name || "Зона ограничений").replace(/\s*\([^)]*\)/g, "").slice(0, 32);
  const title = temporary ? "Временная зона" : height ? "Ограничение" : "Запретная зона";
  return {className: `zone-label ${temporary ? "zone-temporary" : height ? "zone-height" : "zone-ban"}`,
    html: `<span class="zone-label-content">${icon(height || temporary ? "alert" : "ban")}<span><b>${title}</b><small>${esc(detail)}</small></span></span>`};
}
const WORK_KINDS = new Set(["survey_area", "site", "reserve_site", "no_fly", "airspace"]);
function areaKm2(gj) {
  // площадь областей съемки (сферический эксцесс не нужен — участки малы)
  let a = 0;
  for (const f of gj.features.filter((x) => x.properties.kind === "survey_area")) {
    const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.type === "MultiPolygon" ? f.geometry.coordinates : [];
    for (const poly of polys) poly.forEach((ring, ri) => {
      const lat0 = ring[0][1] * Math.PI / 180, kx = 111.32 * Math.cos(lat0), ky = 110.57;
      let s = 0; for (let i = 1; i < ring.length; i++) s += (ring[i - 1][0] * kx) * (ring[i][1] * ky) - (ring[i][0] * kx) * (ring[i - 1][1] * ky);
      a += (ri === 0 ? 1 : -1) * Math.abs(s) / 2;
    });
  }
  return a;
}
function drawScenario(gj) {
  const labelledZones = new Set(gj.features.filter(f => {
    const p = f.properties || {};
    return p.kind === "no_fly" && (p.alt_max_m == null || p.alt_max_m <= 700 || (p.alt_min_m != null && p.alt_min_m <= 700));
  }).slice(0, 8));
  const surveyLayer = L.geoJSON({type: "FeatureCollection", features: gj.features.filter(f => f.properties.kind === "survey_area")},
    {style: styleScenario, interactive: false});
  const objects = L.geoJSON({type: "FeatureCollection", features: gj.features.filter(f => f.properties.kind !== "survey_area")}, {
    style: styleScenario,
    pointToLayer: (f, ll) => f.properties.kind === "obstacle"
      ? L.circleMarker(ll, { radius: 5, color: "#111827", fillColor: "#facc15", fillOpacity: 1, weight: 1.5, renderer: canvasR })
      : L.marker(ll, { icon: siteIcon(f.properties.kind === "reserve_site" ? "reserve" : "main") }),
    onEachFeature: (f, l) => {
      l.bindPopup(popupScenario(f.properties));
      if (labelledZones.has(f) && l.bindTooltip) {
        const label = zoneLabel(f.properties);
        l.bindTooltip(label.html, {permanent: true, direction: "center", className: label.className, opacity: 0.97, interactive: false});
      }
    },
  });
  const layer = L.layerGroup([surveyLayer, objects]);
  setLayer("scenario", layer);
  drawExtraSites();
  map.invalidateSize();
  S.userMoved = false;
  fitScenario();
  const c = {};
  gj.features.forEach((f) => { c[f.properties.kind] = (c[f.properties.kind] || 0) + 1; });
  const pill = (col, n, t) => n ? `<span class="pill"><i style="background:${col}"></i>${n} ${t}</span>` : "";
  const area = areaKm2(gj);
  $("#scenario-card").innerHTML = `<div class="sc-name">${esc(gj.name || "Сценарий")}</div>
    <div class="sc-meta">${area ? `площадь съемки ${fmt(area, area < 10 ? 1 : 0)} км²` : ""}${S.scenarioId && S.scenarioId.startsWith("plan:") ? " · из истории расчетов" : ""}</div>
    <div class="sc-stats">${pill("#2563eb", c.survey_area, plural(c.survey_area || 0, "область", "области", "областей"))}
    ${pill("#dc2626", c.no_fly, plural(c.no_fly || 0, "зона", "зоны", "зон"))}${pill("#facc15", c.obstacle, "препятств.")}
    ${pill("#0f172a", c.site, plural(c.site || 0, "площадка", "площадки", "площадок"))}${pill("#64748b", c.reserve_site, "резерв")}
    ${S.extraSites.length ? pill("#2563eb", S.extraSites.length, "добавлено") : ""}
    ${!c.site && !S.extraSites.length ? `<span class="pill"><i style="background:#0891b2"></i>площадки: авто</span>` : ""}</div>`;
}
function drawExtraSites() {
  const rg = L.layerGroup();
  S.extraSites.forEach((st, i) => {
    const mk = L.marker([st.lat, st.lon], { icon: siteIcon("extra"), draggable: true }).addTo(rg);
    mk.bindPopup(`<div class="pp-t">${esc(st.name)}</div><div class="hint">${st.manual ? "Поставлена вручную" : "Добавлена по рекомендации"} · перетащите, чтобы сдвинуть</div>
      <button class="btn btn-sm danger" style="margin-top:8px" data-rm="${i}">${icon("trash")}Удалить площадку</button>`);
    mk.on("popupopen", (e) => { const b = e.popup.getElement().querySelector("[data-rm]"); if (b) b.onclick = () => { S.extraSites.splice(i, 1); map.closePopup(); invalidatePlan(); drawScenario(S.scenario); }; });
    mk.on("dragend", () => { const ll = mk.getLatLng(); st.lat = ll.lat; st.lon = ll.lng; invalidatePlan(); drawExtraSites(); toast("Площадка сдвинута — пересчитайте план"); });
  });
  (S.plan?.sites || []).filter((x) => x.auto).forEach((st) => L.marker([st.lat, st.lon], {
    icon: L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13], html: `<div class="site-ico auto">A</div>` }) })
    .bindPopup(`<div class="pp-t">${esc(st.name)}</div><div class="hint">Расставлена автоматически: в задании нет площадок</div>`).addTo(rg));
  const showRecs = S.plan && S.plan.status !== "ok";
  (S.plan?.recommendations || []).forEach((rec, ri) => (rec.sites || []).forEach((st) => {
    if (S.extraSites.some((x) => Math.abs(x.lat - st.lat) < 1e-6 && Math.abs(x.lon - st.lon) < 1e-6)) return;
    if (!showRecs && S.recHover !== ri) return;
    L.marker([st.lat, st.lon], { icon: L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 24], html: `<div style="color:#2563eb;width:26px;height:26px;filter:drop-shadow(0 1px 2px rgba(0,0,0,.4))">${icon("pin")}</div>` }) })
      .bindPopup(`<div class="pp-t">Рекомендованная площадка</div>${esc(rec.text || rec.reason)}`).addTo(rg);
  }));
  if (rg.getLayers().length) setLayer("recs", rg); else dropLayer("recs");
}
function fitScenario() {
  // вписываем район работ (области, зоны, площадки), а не все препятствия региона
  try {
    if (!S.scenario) return;
    // область и площадки; зоны региональных файлов тянутся далеко за район работ
    let work = S.scenario.features.filter((f) => ["survey_area", "site", "reserve_site"].includes(f.properties.kind));
    if (!work.length) work = S.scenario.features.filter((f) => WORK_KINDS.has(f.properties.kind));
    const b = L.geoJSON({ type: "FeatureCollection", features: work.length ? work : S.scenario.features }).getBounds();
    const pad = window.innerWidth > 860 ? [40, 40] : [16, 16];
    map.fitBounds(b, { paddingTopLeft: pad, paddingBottomRight: [pad[0], pad[1] + ($("#inspector").hidden ? 0 : 0)], animate: false });
  } catch (e) {}
}
// пока пользователь сам не двигал карту — держим участок вписанным при любом изменении размера (док, панель, окно)
S.userMoved = false;
map.on("dragstart", () => { S.userMoved = true; });
$("#map").addEventListener("wheel", () => { S.userMoved = true; }, { passive: true });
new ResizeObserver(() => { map.invalidateSize(); if (!S.userMoved) fitScenario(); }).observe($("#map"));
function setStartFromScenario(gj) {
  const hint = gj.planning_hint && gj.planning_hint.start_time;
  // часовой пояс района: из подсказки сценария, иначе по долготе центра области (15° = 1 ч, округление)
  let tz = tzFromIso(hint);
  if (tz === null) {
    try {
      const work = gj.features.filter((f) => f.properties.kind === "survey_area");
      const c = L.geoJSON({ type: "FeatureCollection", features: work }).getBounds().getCenter();
      tz = Math.max(-12, Math.min(14, Math.round(c.lng / 15))) * 60;
    } catch (e) { tz = 180; }
  }
  S.tzStart = tz;
  $("#tz-label").textContent = tzLabel(tz);
  let d;
  if (hint) d = hint.slice(0, 16);
  else { const t = new Date(Date.now() + 86400000); t.setHours(9, 0, 0, 0); d = new Date(t - t.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
  $("#start").value = d; summarize();
}

// ================================================================ API и расчет
async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : (j.detail || []).map((x) => x.msg).join("; "); } catch (e) {}
    throw new Error(msg || `HTTP ${r.status}`);
  }
  return r.json();
}
class Cancelled extends Error {}
function runPanel(show, title) {
  $("#run-panel").hidden = !show;
  if (!show) return;
  $("#rp-title").textContent = title;
  $("#rp-msg").textContent = "Постановка в очередь…";
  $("#rp-time").textContent = "0 с";
  $("#rp-fill").style.width = "0%";
  $("#rp-stages").innerHTML = STAGES.map(([k, t]) => `<li data-k="${k}">${t}</li>`).join("");
  $("#rp-chart").innerHTML = "";
  $("#map-hint").hidden = true;
}
function updateRunPanel(st, objective) {
  $("#rp-msg").textContent = st.message || "";
  $("#rp-time").textContent = `${fmt(st.elapsed_s, 0)} с`;
  $("#rp-fill").style.width = `${Math.round((st.progress || 0) * 100)}%`;
  const idx = STAGES.findIndex(([k]) => k === st.stage);
  $$("#rp-stages li").forEach((li, i) => { li.className = i < idx ? "done" : i === idx ? "cur" : ""; });
  const tr = st.trace || [];
  if (tr.length < 2) return;
  const W = 488, H = 96, l = 36, r = 6, t = 8, b = 16;
  const col = objective === "flight" ? 2 : 1;
  const ys = tr.map((x) => x[col]), n = tr[tr.length - 1][0];
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y1 - y0 < 1e-6) { y1 += 0.5; y0 -= 0.5; }
  const X = (v) => l + (W - l - r) * (v - 1) / Math.max(n - 1, 1), Y = (v) => t + (H - t - b) * (1 - (v - y0) / (y1 - y0));
  let d = `M${X(tr[0][0])},${Y(ys[0])}`;
  for (let i = 1; i < tr.length; i++) d += `H${X(tr[i][0])}V${Y(ys[i])}`;
  const bi = tr.find((x) => x[3] === 1);
  const unit = "ч";
  $("#rp-chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <line class="grid" x1="${l}" x2="${W - r}" y1="${Y(y1)}" y2="${Y(y1)}"/><line class="grid" x1="${l}" x2="${W - r}" y1="${Y(y0)}" y2="${Y(y0)}"/>
    <text class="ax" x="${l - 4}" y="${Y(y1) + 3}" text-anchor="end">${fmt(y1, 1)} ч</text><text class="ax" x="${l - 4}" y="${Y(y0) + 3}" text-anchor="end">${fmt(y0, 1)} ч</text>
    <path class="ar" d="${d}V${H - b}H${X(tr[0][0])}Z"/><path class="ln" d="${d}"/>
    ${bi ? `<line class="by" x1="${X(bi[0])}" x2="${X(bi[0])}" y1="${t}" y2="${H - b}"/><text class="byl" x="${X(bi[0]) + 4}" y="${t + 9}">байес (TPE)</text>` : ""}
    <text class="ax" x="${l}" y="${H - 3}">1</text><text class="ax" x="${W - r}" y="${H - 3}" text-anchor="end">${n} вариантов</text></svg>
    <div class="rp-stats"><span>лучший вариант: <b>${fmt(ys[ys.length - 1], 2)} ${unit}</b> ${objective === "flight" ? "налета" : "работ"}</span><span>оценено: <b>${st.evaluations}</b></span></div>`;
}
async function runPlan(objective, label, request) {
  if (!request.scenario) throw new Error("Сценарий не загружен");
  if (!request.params.fleet.some((x) => x.count > 0)) throw new Error("Добавьте хотя бы один борт");
  runPanel(true, label);
  const job = await api("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario: request.scenario, params: { ...request.params, objective } }) });
  S.job = job.job_id;
  try {
    if (S.cancelRequested || request.revision !== S.revision) { await cancelRun(); throw new Cancelled("Расчет отменен"); }
    for (;;) {
      await sleep(350);
      const st = await api(`/api/jobs/${job.job_id}`);
      if (request.revision !== S.revision) { await cancelRun(); throw new Cancelled("Задание изменено"); }
      updateRunPanel(st, objective);
      if (st.status === "done") return api(`/api/plans/${st.plan_id}`);
      if (st.status === "error") throw new Error(st.error);
      if (st.status === "cancelled") throw new Cancelled("Расчет отменен");
    }
  } finally { S.job = null; }
}
async function cancelRun() { S.cancelRequested = true; if (S.job) { try { await api(`/api/jobs/${S.job}/cancel`, { method: "POST" }); } catch (e) {} } }
$("#rp-cancel").addEventListener("click", cancelRun);
let busy = false;
let missionRunAttempts = Math.min(2, Math.max(0, Number(store.get("onboarding.attempts", 0)) || 0));
function updateOnboarding() { $("#first-mission").hidden = missionRunAttempts >= 2; }
function recordMissionRunAttempt() {
  if (missionRunAttempts < 2) {
    missionRunAttempts++;
    store.set("onboarding.attempts", missionRunAttempts);
  }
  updateOnboarding();
}
async function onRun(objectives) {
  if (busy) return; busy = true;
  S.cancelRequested = false;
  recordMissionRunAttempt();
  $$("#run, #run2, #compare, #hint-run").forEach((b) => { b.disabled = true; });
  try {
    const request = { scenario: structuredClone(S.scenario), params: params(), revision: S.revision };
    const plans = [];
    for (const o of objectives) {
      if (S.cancelRequested || request.revision !== S.revision) throw new Cancelled("Расчет отменен");
      plans.push(await runPlan(o, objectives.length > 1 ? `Сравнение: ${objectiveName(o)} (${plans.length + 1}/${objectives.length})` : `Расчет: ${objectiveName(o)}`, request));
    }
    if (S.cancelRequested || request.revision !== S.revision) throw new Cancelled("Расчет отменен");
    S.cmp = objectives.length > 1 ? plans.filter((p) => p.summary && p.summary.makespan_h !== undefined) : null;
    loadPlan(plans.find((p) => p.params?.objective === S.objective) || plans[0]);
    switchTab("overview");
    const p = S.plan;
    if (p.status === "ok" && p.validation?.passed) toast(`План готов за ${fmt(p.summary.compute_s)} с: ${fmt(p.summary.makespan_h)} ч работ, ${p.summary.sorties} ${plural(p.summary.sorties, "вылет", "вылета", "вылетов")}, crash-тест пройден`, "ok", 6000);
    else toast(p.error || "План невыполним — см. рекомендации", "bad", 9000);
  } catch (e) {
    if (e instanceof Cancelled) toast("Расчет отменен"); else toast(e.message, "bad", 9000);
  } finally {
    busy = false; $$("#run, #run2, #compare, #hint-run").forEach((b) => { b.disabled = false; }); runPanel(false);
  }
}

// ================================================================ подбор парка к сроку
async function onAdvise() {
  if (busy) return;
  if (!S.scenario) { toast("Сценарий не загружен", "bad"); return; }
  if (!$("#deadline").value) { toast("Задайте срок выполнения — подберу минимальный парк, который в него укладывается; без срока покажу кривую «время от числа бортов»"); }
  busy = true;
  S.cancelRequested = false;
  const revision = S.revision;
  $$("#run, #run2, #compare, #advise").forEach((b) => { b.disabled = true; });
  runPanel(true, "Подбор парка к сроку");
  try {
    const job = await api("/api/advise", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario: S.scenario, params: params("time") }) });
    S.job = job.job_id;
    if (S.cancelRequested || revision !== S.revision) { await cancelRun(); throw new Cancelled("Подбор отменен"); }
    let st;
    for (;;) {
      await sleep(400);
      st = await api(`/api/jobs/${job.job_id}`);
      if (revision !== S.revision || S.cancelRequested) { await cancelRun(); throw new Cancelled("Подбор отменен"); }
      updateRunPanel(st, "time");
      if (st.status === "done") break;
      if (st.status === "error") throw new Error(st.error);
      if (st.status === "cancelled") throw new Cancelled("Подбор отменен");
    }
    renderAdvice(st.advice);
  } catch (e) { if (e instanceof Cancelled) toast("Подбор отменен"); else toast(e.message, "bad", 9000); }
  finally { S.job = null; busy = false; $$("#run, #run2, #compare, #advise").forEach((b) => { b.disabled = false; }); runPanel(false); }
}
$("#advise").addEventListener("click", onAdvise);
function renderAdvice(a) {
  const box = $("#advice");
  if (!a || !a.points.length) { box.innerHTML = `<div class="advice-card"><div class="advice-t">${esc(a?.text || "Нет вариантов")}</div></div>`; return; }
  const P = a.points.filter((x) => x.feasible);
  const W = 360, H = 150, l = 34, r = 8, t = 10, b = 22;
  const n1 = a.points[a.points.length - 1].n;
  let y1 = Math.max(...P.map((x) => x.makespan_h), a.deadline_h || 0) * 1.08, y0 = 0;
  const X = (v) => l + (W - l - r) * (v - 1) / Math.max(n1 - 1, 1), Y = (v) => t + (H - t - b) * (1 - (v - y0) / (y1 - y0));
  let d = "";
  P.forEach((x, i) => { d += `${i ? "L" : "M"}${X(x.n)},${Y(x.makespan_h)}`; });
  const rec = a.recommended;
  const dots = P.map((x) => `<circle cx="${X(x.n)}" cy="${Y(x.makespan_h)}" r="${rec && x.n === rec.n ? 5 : 3}" fill="${rec && x.n === rec.n ? "var(--ok)" : "var(--accent)"}"><title>${x.n} ${plural(x.n, "борт", "борта", "бортов")} (задействовано ${x.used}): ${fmt(x.makespan_h)} ч работ, налет ${fmt(x.flight_h)} ч</title></circle>`).join("");
  let ax = "";
  for (let n = 1; n <= n1; n++) if (n1 <= 12 || n % 2 === 1) ax += `<text class="ax" x="${X(n)}" y="${H - 6}" text-anchor="middle">${n}</text>`;
  const fl = (f) => Object.entries(f).filter(([, v]) => v).map(([k, v]) => `${v}×${SHORT[k] || k}`).join(" + ");
  box.innerHTML = `<div class="advice-card"><div class="label" style="margin-top:0">Время работ от числа бортов <span class="hint">оценка</span></div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line class="grid" x1="${l}" x2="${W - r}" y1="${Y(0)}" y2="${Y(0)}"/>
      <text class="ax" x="${l - 4}" y="${Y(y1 / 1.08) + 3}" text-anchor="end">${fmt(y1 / 1.08, 0)} ч</text><text class="ax" x="${l - 4}" y="${Y(0) + 3}" text-anchor="end">0</text>
      ${a.deadline_h ? `<line class="dl" x1="${l}" x2="${W - r}" y1="${Y(a.deadline_h)}" y2="${Y(a.deadline_h)}"/><text class="dll" x="${W - r}" y="${Y(a.deadline_h) - 4}" text-anchor="end">срок ${fmt(a.deadline_h)} ч</text>` : ""}
      <path class="ln" d="${d}"/>${dots}${ax}</svg>
    <div class="advice-t">${esc(a.text)}</div>
    <div class="advice-a">${rec ? `<button class="btn btn-sm btn-primary" id="adv-apply">${icon("play")}Взять ${fl(rec.fleet)} и рассчитать</button>` : ""}
      ${a.knee && (!rec || a.knee.used !== rec.used) ? `<button class="btn btn-sm" id="adv-knee">Взять ${fl(a.knee.fleet)}</button>` : ""}</div></div>`;
  const apply = (f, run) => { Object.keys(S.fleet).forEach((k) => { S.fleet[k] = f[k] || 0; }); store.set("fleet", S.fleet); renderFleet(); invalidatePlan(); if (run) onRun([S.objective]); else toast("Парк обновлен"); };
  if (rec) $("#adv-apply").onclick = () => apply(rec.fleet, true);
  if ($("#adv-knee")) $("#adv-knee").onclick = () => apply(a.knee.fleet, false);
}

// ================================================================ результат
function switchTab(t) {
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === t ? "true" : "false"));
  $$(".tab-body").forEach((s) => { s.hidden = s.id !== `tab-${t}`; });
  if (t === "history") refreshHistory();
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
function updatePanelAlert(plan) {
  const button = $("#panel-alert");
  const failed = plan?.validation?.checks?.filter(check => !check.passed).length || 0;
  const warnings = plan?.warnings?.length || 0;
  const late = !!(plan?.params?.deadline_h && plan?.summary?.makespan_h > plan.params.deadline_h);
  const infeasible = !!(plan && plan.status !== "ok");
  const count = failed + warnings + Number(late || infeasible);
  button.hidden = !count;
  if (!count) return;
  button.classList.toggle("critical", !!failed || infeasible);
  const messages = [infeasible && !failed ? "План требует внимания" : "", failed ? `${failed} ${plural(failed, "нарушение", "нарушения", "нарушений")}` : "",
    warnings ? `${warnings} ${plural(warnings, "замечание", "замечания", "замечаний")}` : "",
    late ? "срок превышен" : ""].filter(Boolean);
  button.innerHTML = `${ico("alert")}<span><strong>${esc(messages.join(" · ") || "План требует внимания")}</strong><small>Открыть проверки и рекомендации</small></span>${ico("chevR")}`;
}
$("#panel-alert").addEventListener("click", () => {
  switchTab("result");
  const target = $("#result .check.bad") || $("#result .banner") || $("#result .step:last-child");
  target?.scrollIntoView({behavior: "smooth", block: "start"});
});

function loadPlan(plan) {
  stopPlayback();
  S.plan = plan;
  if (plan.params?.objective) {
    S.objective = plan.params.objective;
    setChip("#objective", S.objective);
    summarize();
  }
  const tz = tzFromIso(plan.summary && plan.summary.mission_start_iso);
  if (tz !== null) TZ_MIN = tz;
  S.sel = null; S.showOtherDrones = false; S.showFutureRoutes = false; S.hidden = new Set(); S.sortieFilter = ""; S.routeFocus = null; S.timelineDrone = "";
  S.progress = null; S.coverageShown = 0; S.timelineEvents = []; S.timelineGroups = []; S.highlightedEvent = null;
  S.colors = {};
  (plan.drones || []).forEach((d, i) => { S.colors[d.id] = d.color || PALETTE[i % PALETTE.length]; });
  legend();
  $("#inspector").hidden = true;
  $("#map-hint").hidden = true;
  renderResult(plan);
  renderRouteFocus();
  updatePanelAlert(plan);
  drawPlan(plan);
  setFutureRoutes(false);
  setupTimeline(plan);
  loadCoverage(plan);
  window.product?.loadReport(plan);
}
function clearPlan() {
  stopPlayback();
  S.plan = null; S.sel = null; S.cmp = null; S.recHover = null; S.progress = null; S.coverageShown = 0;
  legend();
  S.timelineEvents = []; S.timelineGroups = []; S.highlightedEvent = null; S.routeFocus = null; S.timelineDrone = "";
  $("#timeline-markers").innerHTML = ""; $("#timeline-event-popover").hidden = true;
  ["routes", "future", "coverage", "uncovered", "drones", "checks", "sel"].forEach(dropLayer);
  $("#dock").hidden = true; $("#inspector").hidden = true;
  $("#dock").classList.remove("expanded"); $("#dock-expand").setAttribute("aria-pressed", "false");
  renderRouteFocus(); updatePanelAlert(null);
  $("#result").innerHTML = ""; $("#result-empty").hidden = false; $("#res-badge").hidden = true;
  renderOverlayList();
  drawExtraSites();
  window.product?.clearReport();
}
function invalidatePlan() {
  S.revision++;
  if (busy) cancelRun();
  clearPlan();
  $("#advice").innerHTML = "";
}
function canExport(r) { return r?.status === "ok" && r.validation?.passed && r.sorties?.length; }
function siteName(prefix) {
  const names = new Set([...(S.scenario?.features || []).map((f) => f.properties?.name), ...S.extraSites.map((s) => s.name)]);
  let n = 1; while (names.has(`${prefix} ${n}`)) n++;
  return `${prefix} ${n}`;
}

const dur = (iso1, iso2) => `${iso1.slice(11, 16)}–${iso2.slice(11, 16)}`;
function deltaChip(cur, other, lowerBetter = true) {
  if (other === undefined || other === null || !cur) return "";
  if (!other) return "";
  const d = (cur - other) / other * 100;
  if (Math.abs(d) < 0.5) return "";
  const good = lowerBetter ? d < 0 : d > 0;
  return `<span class="delta ${good ? "good" : "worse"}" title="относительно другого критерия">${d > 0 ? "+" : "−"}${fmt(Math.abs(d), 0)}%</span>`;
}
function flightGeometry(s) {
  const headings = [...new Set((s.headings_deg || []).filter(Number.isFinite))].sort((a, b) => a - b);
  const layers = [...new Set((s.transit_layers_agl_m || []).filter(Number.isFinite))].sort((a, b) => a - b);
  const height = layers.length ? `${fmt(layers[0], 0)}${layers.length > 1 ? "–" + fmt(layers[layers.length - 1], 0) : ""} м AGL` : "высоты не указаны";
  const pills = (values, unit) => values.map(x => `<span class="geometry-value">${fmt(x, 1)}${unit}</span>`).join("");
  return `<details class="flight-geometry">
    <summary><span class="geometry-icon">${ico("route")}</span><span class="geometry-summary"><b>Геометрия маршрутов</b><span>${headings.length} ${plural(headings.length, "направление", "направления", "направлений")} · ${height}</span></span>${ico("chev")}</summary>
    <div class="geometry-body">
      <div class="geometry-label">Курсы галсов <span class="badge">${headings.length}</span></div>
      <div class="geometry-values" tabindex="0" role="region" aria-label="Все курсы галсов">${pills(headings, "°") || '<span class="hint">Нет данных</span>'}</div>
      <div class="geometry-label">Эшелоны перелёта · AGL</div>
      <div class="geometry-values" tabindex="0" role="region" aria-label="Все эшелоны перелёта">${pills(layers, " м") || '<span class="hint">Нет данных</span>'}</div>
      <div class="geometry-terrain"><span>Рельеф</span><span>${esc(s.terrain || "Нет данных")}</span></div>
    </div>
  </details>`;
}
function renderResult(r) {
  $("#result-empty").hidden = true;
  const el = $("#result");
  const badge = $("#res-badge");
  if (!r.summary || !r.sorties?.length) {
    badge.hidden = false; badge.className = "badge bad"; badge.textContent = "!";
    el.innerHTML = `<div class="banner bad">${ico("alert")}<div><div class="banner-t">План невыполним</div><div class="banner-s">${esc(r.error || "")}</div></div></div>${recsHtml(r)}${warnHtml(r)}`;
    bindRecs(r); return;
  }
  const s = r.summary, v = r.validation || { checks: [] };
  const passed = v.checks.filter((c) => c.passed).length;
  const ok = r.status === "ok" && v.passed;
  const partial = r.status === "infeasible" && v.passed;
  badge.hidden = false; badge.className = "badge" + (ok ? "" : v.passed ? " warn" : " bad"); badge.textContent = ok ? "✓" : "!";
  const bannerCls = ok ? "ok" : (v.passed ? "warn" : "bad");
  const late = r.params?.deadline_h && s.makespan_h > r.params.deadline_h;
  const bannerT = ok ? (late ? `Нарушений нет, но срок ${fmt(r.params.deadline_h)} ч не выдерживается` : "План выполним, нарушений нет")
    : partial ? "Задание выполнимо частично — см. рекомендации" : "Найдены нарушения — см. crash-тест";
  const other = (S.cmp || []).find((p) => p.id !== r.id && p.scenario_name === r.scenario_name);
  const os = other && other.summary;
  const kpi = (val, u, l, extra = "") => `<div class="kpi"><div class="kpi-v">${val}<small>${u}</small>${extra}</div><div class="kpi-l">${l}</div></div>`;
  const models = Object.values(r.models);
  const shares = Object.entries(s.shares || {}).filter(([, x]) => x > 0.001);
  const tags = [objectiveName(s.objective), s.optimizer === "bayes" ? "байесовская оптимизация" : "сетка",
    s.wind_model === "physical" ? "физическая модель ветра" : "ветер по методике QA", s.energy_model === "calibrated" ? "АКБ по телеметрии" : "АКБ линейно",
    s.airspace_mode === "no_flight_plan" ? "без плана полета ≤150 м" : "коридор до 700 м", `расчет ${fmt(s.compute_s)} с`];
  el.innerHTML = `
    <div class="banner ${bannerCls}">${ico(ok ? "ok" : "alert")}
      <div><div class="banner-t">${bannerT}</div>
      <div class="banner-s">Crash-тест ${passed}/${v.checks.length} · план ${esc(r.id)} · ${esc(s.first_takeoff_iso.slice(0, 16).replace("T", " "))} → ${esc(s.mission_end_iso.slice(11, 16))}</div>
      ${partial ? `<div class="banner-s">Покрыто ${fmt(s.coverage_allowed_pct)}% разрешенной площади. Построенные маршруты прошли проверку.</div>` : ""}
      <div class="tagline">${tags.map((x) => `<span>${esc(x)}</span>`).join("")}</div>
      ${late ? `<button class="btn btn-sm" style="margin-top:8px" id="late-advise">${icon("sparkle")}Сколько бортов нужно к сроку?</button>` : ""}</div></div>

    ${compareCard(r)}

    <div class="kpis">
      <div class="kpi hero">
        <div><div class="kpi-v">${fmt(s.makespan_h)}<small>ч</small>${deltaChip(s.makespan_h, os?.makespan_h)}</div><div class="kpi-l">время работ · ${s.work_days} ${plural(s.work_days, "день", "дня", "дней")}</div></div>
        <div><div class="kpi-v">${fmt(s.flight_hours)}<small>ч</small>${deltaChip(s.flight_hours, os?.flight_hours)}</div><div class="kpi-l">суммарный налет</div></div>
      </div>
      ${kpi(s.sorties, plural(s.sorties, "вылет", "вылета", "вылетов"), `бортов в работе ${s.drones_used} из ${s.drones_total}`, deltaChip(s.sorties, os?.sorties))}
      ${kpi(fmt(s.coverage_allowed_pct), "%", `покрытие разрешенной площади ${fmt(s.area_allowed_km2)} км²`)}
      ${kpi(fmt(s.total_km, 0), "км", `путь, из них съемка ${fmt(s.survey_km, 0)} км`)}
      ${kpi(fmt(s.photos, 0), "", "снимков (оценка)")}
    </div>
    ${shares.length > 1 ? `<div class="shares">${shares.map(([k, x]) => `<i style="width:${x * 100}%;background:${MODEL_COLORS[k] || "#64748b"}" title="${esc(r.models[k]?.title || k)}: ${fmt(x * 100, 0)}%"></i>`).join("")}</div>
      <div class="shares-l">${shares.map(([k, x]) => `<span style="--c:${MODEL_COLORS[k] || "#64748b"}">${esc((r.models[k]?.title || k).replace("Геоскан ", ""))} ${fmt(x * 100, 0)}%</span>`).join("")}<span class="hint" style="margin-left:auto">доли площади</span></div>` : ""}
    ${flightGeometry(s)}

    <div class="section-h">Crash-тест · независимая проверка <span class="badge ${v.passed ? "" : "bad"}">${passed}/${v.checks.length}</span></div>
    <div class="checks">${v.checks.map((c, ci) => `<details class="check ${c.passed ? "ok" : "bad"}" ${c.passed ? "" : "open"}><summary><span class="dot-s">${ico(c.passed ? "check" : "x")}</span>${esc(c.name)}
      <span class="check-v">${checkValue(c)}</span></summary>
      <div class="check-d">${(c.details || []).map(esc).join("<br>")}${(c.points || []).length ? `<div>${c.points.slice(0, 6).map((p, pi) => `<button class="go" data-c="${ci}" data-p="${pi}">${ico("target")}${c.passed ? "Показать на карте" : `#${p.sortie ?? ""} на карте`}</button>`).join("")}</div>` : ""}</div></details>`).join("")}</div>

    ${recsHtml(r)}

    ${ok ? `<div class="section-h">Файлы миссии</div>
    <div class="mission-exports">
      <a class="export-card export-bundle" href="/api/plans/${r.id}/export/zip"><span class="export-icon">${ico("download")}</span><span class="export-copy"><strong>Скачать комплект</strong><span>ZIP · маршруты, задания и отчёт</span></span><span class="export-tag">ZIP</span></a>
      <div class="export-formats">
        <a class="export-format" href="/api/plans/${r.id}/export/kml">${ico("map")}<strong>KML</strong><span>Для карт</span></a>
        <a class="export-format" href="/api/plans/${r.id}/export/geojson">${ico("code")}<strong>GeoJSON</strong><span>Геоданные</span></a>
        <a class="export-format" href="/api/plans/${r.id}/export/csv">${ico("file")}<strong>CSV</strong><span>Расписание</span></a>
      </div>
      <a class="export-card export-print" href="/api/plans/${r.id}/brief" target="_blank" rel="noopener"><span class="export-icon">${ico("printer")}</span><span class="export-copy"><strong>Печатные задания</strong><span>Маршруты, этапы и ключевые точки</span></span>${ico("chevR")}</a>
      <div class="export-footnote">В комплекте — QGC .plan по вылетам и файлы каждого борта.</div>
    </div>` : ""}

    <div class="section-h">Борта <span class="hint">задание борта — ${icon("file").replace("<svg", '<svg style="width:12px;height:12px;vertical-align:-2px"')} KML · ${icon("printer").replace("<svg", '<svg style="width:12px;height:12px;vertical-align:-2px"')} печать</span></div>
    <div class="list-box" id="drones"></div>

    ${(s.maintenance || []).length ? `<div class="section-h maintenance-heading">Ресурс до ТО <span class="hint">налет связан с регламентом ТО</span></div>
    <div class="table-box maintenance-table-box"><table class="t compact maintenance-table"><thead><tr><th scope="col">Тип</th><th scope="col" class="num">Вылетов</th><th scope="col" class="num">Налет, ч</th><th scope="col">Интервал / расход ТО</th></tr></thead><tbody>
      ${s.maintenance.map((m) => `<tr title="${esc(m.note || "")}"><td><span class="sw" style="background:${MODEL_COLORS[m.model] || "#64748b"}"></span>${esc(m.title.replace("Геоскан ", ""))}${m.note ? " *" : ""}</td>
        <td class="num">${m.sorties}</td><td class="num">${fmt(m.flight_h)}</td><td>${esc(m.interval)}<span class="maintenance-used">Израсходовано ${fmt(m.used_pct)}%</span></td></tr>`).join("")}
    </tbody></table></div>${s.maintenance.some((m) => m.note) ? `<div class="hint" style="margin-top:4px">* ${esc(s.maintenance.find((m) => m.note).note)}</div>` : ""}` : ""}

    <div class="section-h">Параметры съемки</div>
    <div class="table-box"><table class="t compact"><tr><th>Тип</th><th class="num">H, м</th><th class="num">Шаг, м</th><th class="num">V, м/с</th><th class="num">R, км</th></tr>
      ${models.map((m) => `<tr><td><span class="sw" style="background:${MODEL_COLORS[m.model] || "#64748b"}"></span><b>${esc(m.title.replace("Геоскан ", ""))}</b><div class="hint" title="${esc(m.payload_title)}">GSD ${fmt(m.gsd_cm, 1)} см · ${esc(m.payload_title)}</div></td><td class="num">${fmt(m.altitude_agl_m, 0)}</td>
        <td class="num">${fmt(m.line_spacing_m, 0)}</td><td class="num">${fmt((s.survey_speed_by_model_ms || {})[m.model]?.[0] ?? m.survey_speed_ms)}</td><td class="num">${fmt(m.radius_km)}</td></tr>`).join("")}
    </table></div>

    <div class="section-h">Вылеты <span class="filter"><select id="sfilter" aria-label="Фильтр по борту"><option value="">Все борта</option>${r.drones.filter((d) => d.sorties.length).map((d) => `<option>${esc(d.id)}</option>`).join("")}</select></span></div>
    <div class="table-box"><table class="t" id="sorties"></table></div>

    ${warnHtml(r)}`;
  renderDrones(); renderSorties(); bindRecs(r);
  if ($("#late-advise")) $("#late-advise").onclick = () => { switchTab("task"); $$("details.step")[3].open = true; onAdvise(); };
  $("#sfilter").addEventListener("change", (e) => { S.sortieFilter = e.target.value; renderSorties(); });
  $$(".go", el).forEach((b) => b.addEventListener("click", (e) => { e.preventDefault(); goToPoint(v.checks[+b.dataset.c].points[+b.dataset.p]); }));
  $$(".cmp-show", el).forEach((b) => b.addEventListener("click", () => { const p = S.cmp.find((x) => x.id === b.dataset.id); if (p) { loadPlan(p); switchTab("result"); } }));
}
function checkValue(c) {
  if (typeof c.value !== "number") return "";
  if (c.name.includes("сближ")) return `мин. ${fmt(c.value, 0)} м`;
  if (c.name.includes("Энергия")) return `резерв ≥ ${fmt(c.value, 0)}%`;
  return c.passed ? "" : `${c.value}`;
}
function compareCard(r) {
  const pl = (S.cmp || []).filter((p) => p.scenario_name === r.scenario_name);
  if (pl.length < 2 || !pl.some((p) => p.id === r.id)) return "";
  const best = (k) => pl.filter(p => canExport(p)).reduce((acc, p) => !acc || p.summary[k] < acc.summary[k] ? p : acc, null)?.id;
  const col = (p) => {
    const s = p.summary;
    const row = (lbl, k, u, d = 1) => `<div class="cmp-row"><span>${lbl}</span><b class="${best(k) === p.id ? "best" : ""}">${fmt(s[k], d)} ${u}</b></div>`;
    return `<div class="cmp-col ${p.id === r.id ? "cur" : ""}"><div class="cmp-h">${ico(p.params.objective === "time" ? "clock" : "battery")}${objectiveName(p.params.objective)}</div>
      ${row("Время работ", "makespan_h", "ч")}${row("Налет", "flight_hours", "ч")}${row("Вылетов", "sorties", "", 0)}
      <div class="cmp-row"><span>Crash-тест</span><b class="${p.validation?.passed ? "best" : ""}">${p.validation?.passed ? "✓ пройден" : "✖"}</b></div>
      ${p.id === r.id ? `<button class="btn btn-sm" disabled>На карте</button>` : `<button class="btn btn-sm cmp-show" data-id="${p.id}">Показать этот план</button>`}</div>`;
  };
  const timePlan = pl.find(p => p.params.objective === "time" && canExport(p));
  const balancePlan = pl.find(p => p.params.objective === "balanced" && canExport(p));
  let note = "Сравнение при одинаковых условиях. Лучшие значения отмечены среди выполнимых планов.";
  if (timePlan && balancePlan && timePlan.summary.makespan_h > 0 && timePlan.summary.flight_hours > 0) {
    const change = (key) => { const d = (balancePlan.summary[key] / timePlan.summary[key] - 1) * 100; return `${d < 0 ? "−" : "+"}${fmt(Math.abs(d))}%`; };
    note += ` Баланс относительно режима «Минимум времени»: ${change("makespan_h")} времени, ${change("flight_hours")} налёта.`;
  }
  return `<div class="cmp-card"><div class="cmp-grid ${pl.length > 2 ? "three" : ""}">${pl.map(col).join("")}</div><div class="cmp-foot">${note}</div></div>`;
}
function renderDrones() {
  const r = S.plan;
  $("#drones").innerHTML = r.drones.map((d) => {
    const off = S.hidden.has(d.id), idle = !d.sorties.length;
    return `<div class="drone ${off || idle ? "off" : ""}"><span class="sw" style="background:${S.colors[d.id]};width:12px;height:12px"></span>
      <div><div class="drone-n">${esc(d.id)} <span class="hint">${esc(d.title)}</span></div>
      <div class="drone-s">${idle ? "не задействован — оптимизатору выгоднее другие типы" : `${d.sorties.length} ${plural(d.sorties.length, "вылет", "вылета", "вылетов")} · ${fmt(d.flight_h, 1)} ч · эшелон ${fmt(d.transit_alt_agl_m, 0)} м · загрузка ${fmt(d.utilization_pct, 0)}%`}</div>
      <div class="util"><i style="width:${Math.min(100, d.utilization_pct)}%;background:${S.colors[d.id]}"></i></div></div>
      ${idle ? "<span></span>" : `<div class="drone-a">${canExport(r) ? `<a class="eye" href="/api/plans/${r.id}/export/kml?drone=${encodeURIComponent(d.id)}" title="Задание борта (KML)">${icon("file")}</a>
        <a class="eye" href="/api/plans/${r.id}/brief?drone=${encodeURIComponent(d.id)}" target="_blank" rel="noopener" title="Задание борта для печати">${icon("printer")}</a>
        ` : ""}
        <button class="eye" data-d="${esc(d.id)}" title="${off ? "Показать" : "Скрыть"} на карте">${icon(off ? "eyeoff" : "eye")}</button></div>`}</div>`;
  }).join("");
  $$("#drones button.eye").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.d; S.hidden.has(id) ? S.hidden.delete(id) : S.hidden.add(id);
    renderDrones(); applyRouteStyles(); setTime(S.t); drawGantt();
  }));
}
function renderSorties() {
  const rows = S.plan.sorties.filter((x) => !S.sortieFilter || x.drone_id === S.sortieFilter);
  $("#sorties").innerHTML = `<tr><th>#</th><th>Борт</th><th>Взлет</th><th class="num">мин</th><th class="num">км</th><th class="num">АКБ</th></tr>` +
    rows.map((x) => `<tr class="row ${S.sel === x.id ? "sel" : ""}" data-s="${x.id}"><td>${x.id}</td><td><span class="sw" style="background:${S.colors[x.drone_id]}"></span>${esc(x.drone_id)}</td>
      <td>${esc(x.takeoff_iso.slice(8, 10))}.${esc(x.takeoff_iso.slice(5, 7))} ${esc(x.takeoff_iso.slice(11, 16))}</td><td class="num">${fmt(x.duration_min, 0)}</td>
      <td class="num">${fmt(x.distance_km, 1)}</td><td class="num">${fmt(x.battery_used_pct, 0)}%<span class="mini-bar"><i style="width:${Math.min(100, x.battery_used_pct)}%;background:${x.battery_used_pct > 80 ? "var(--bad)" : "var(--ok)"}"></i></span></td></tr>`).join("");
  $$("#sorties tr.row").forEach((tr) => tr.addEventListener("click", () => selectSortie(+tr.dataset.s, true)));
}
function recsHtml(r) {
  const recs = r.recommendations || [];
  if (!recs.length) return "";
  return `<div class="section-h">Рекомендации</div>` + recs.map((x, i) => `<div class="rec">${ico(x.kind === "optimize" ? "sparkle" : "pin")}<div>
    <div class="rec-t">${esc(x.text || x.reason)}</div>
    ${x.sites && x.sites.length ? `<button class="btn btn-sm btn-primary apply-sites" data-i="${i}">${ico("site")}${x.site_action === "replace" || x.kind === "optimize" ? "Использовать эти площадки" : `Добавить ${x.sites.length > 1 ? "площадки" : "площадку"}`} и пересчитать</button>` : ""}</div></div>`).join("");
}
function bindRecs(r) {
  $$(".rec").forEach((el, i) => {
    el.addEventListener("mouseenter", () => { S.recHover = i; drawExtraSites(); });
    el.addEventListener("mouseleave", () => { S.recHover = null; drawExtraSites(); });
  });
  $$(".apply-sites").forEach((b) => b.addEventListener("click", () => {
    const rec = r.recommendations[+b.dataset.i];
    const replace = rec.site_action === "replace" || rec.kind === "optimize";
    if (replace) {
      S.scenario = { ...S.scenario, features: S.scenario.features.filter((f) => f.properties.kind !== "site") };
      S.extraSites = [];
    }
    rec.sites.forEach((x) => S.extraSites.push({ lon: x.lon, lat: x.lat, name: siteName("Рекомендованная площадка") }));
    invalidatePlan();
    drawScenario(S.scenario);
    toast(`${replace ? "Площадки заменены" : "Добавлено площадок"}: ${rec.sites.length}. Пересчитываю план…`);
    onRun([S.objective]);
  }));
}
function warnHtml(r) {
  const w = r.warnings || [];
  if (!w.length) return "";
  return `<details class="step" style="margin-top:18px"><summary>${ico("info")}<span class="step-t">Замечания планировщика</span><span class="step-s">${w.length}</span></summary>
    <div class="step-body"><ul class="notes">${w.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></details>`;
}

// ================================================================ маршруты на карте
function renderRouteFocus() {
  const control = $("#route-focus");
  const sorties = S.plan?.sorties || [];
  control.disabled = !sorties.length;
  $("#route-focus-hint").textContent = sorties.length ? "Выбор оставит на карте только один вылет." : "После расчёта можно оставить на карте один маршрут.";
  control.innerHTML = '<option value="">Все маршруты</option>' + sorties.map(s =>
    `<option value="${s.id}">Вылет ${s.id} · ${esc(s.drone_id)}</option>`).join("");
  control.value = S.routeFocus === null ? "" : String(S.routeFocus);
}
$("#route-focus").addEventListener("change", e => {
  const id = e.target.value ? Number(e.target.value) : null;
  S.routeFocus = id;
  if (id !== null) {
    const sortie = S.plan?.sorties?.find(s => s.id === id);
    if (sortie) { S.hidden.delete(sortie.drone_id); selectSortie(id, false); }
  } else clearSelection();
  applyRouteStyles(); setTime(S.t); drawGantt(); renderDrones();
});
function routeColor(s, ph) {
  if (S.colorMode === "model") return MODEL_COLORS[s.model] || "#64748b";
  if (S.colorMode === "phase") return PHASE_COLORS[ph] || "#64748b";
  return S.colors[s.drone_id];
}
function routeTimeAtClick(line, latlng) {
  const points = line._fullPts || [], times = line._times || [];
  if (points.length < 2 || points.length !== times.length) return null;
  const click = map.latLngToLayerPoint(latlng);
  let best = Infinity, result = null;
  for (let i = 1; i < points.length; i++) {
    const a = map.latLngToLayerPoint(points[i - 1]), b = map.latLngToLayerPoint(points[i]);
    const dx = b.x - a.x, dy = b.y - a.y;
    const fraction = dx || dy ? Math.max(0, Math.min(1, ((click.x - a.x) * dx + (click.y - a.y) * dy) / (dx * dx + dy * dy))) : 0;
    const distance = (click.x - a.x - fraction * dx) ** 2 + (click.y - a.y - fraction * dy) ** 2;
    if (distance < best) { best = distance; result = times[i - 1] + fraction * (times[i] - times[i - 1]); }
  }
  return result;
}
function drawPlan(r) {
  ["routes", "future", "coverage", "uncovered", "drones", "checks", "sel"].forEach(dropLayer);
  S.routeLines = {}; S.futureLines = {};
  if (!r.sorties) { drawExtraSites(); renderOverlayList(); return; }
  const g = L.layerGroup(), future = L.layerGroup();
  for (const s of r.sorties) {
    const segs = [];
    let cur = null;
    for (let i = 1; i < s.waypoints.length; i++) {
      const a = s.waypoints[i - 1], b = s.waypoints[i];
      const ph = b[5] === "ground" ? a[5] : b[5];
      if (!cur || cur.ph !== ph) { cur = { ph, pts: [[a[1], a[0]]], times: [a[4]] }; segs.push(cur); }
      cur.pts.push([b[1], b[0]]); cur.times.push(b[4]);
    }
    S.routeLines[s.id] = segs.map((sg) => {
      const surv = sg.ph === "survey";
      const pl = L.polyline([], { renderer: routeR, bubblingMouseEvents: false, color: routeColor(s, sg.ph), weight: surv ? 3 : 2, opacity: surv ? 0.85 : 0.55, dashArray: surv ? null : "6 6", _ph: sg.ph, _sid: s.id });
      pl._fullPts = sg.pts; pl._times = sg.times; pl._progressState = null;
      pl.bindTooltip(() => `Вылет ${s.id} · ${esc(s.drone_id)} · ${s.takeoff_iso.slice(11, 16)} · ${PHASE_RU[sg.ph] || sg.ph}`, { sticky: true, direction: "top", offset: [0, -8] });
      pl.on("click", (e) => {
        if (e.originalEvent) L.DomEvent.stopPropagation(e.originalEvent);
        const time = routeTimeAtClick(pl, e.latlng);
        selectSortie(s.id, false);
        if (time !== null) setTime(time);
      });
      pl.on("mouseover", () => hoverSortie(s.id, true));
      pl.on("mouseout", () => hoverSortie(s.id, false));
      pl.addTo(g); return pl;
    });
    S.futureLines[s.id] = segs.map((sg) => L.polyline(sg.pts, { renderer: routeR, color: routeColor(s, sg.ph),
      weight: sg.ph === "survey" ? 2 : 1.5, opacity: 0.14, dashArray: sg.ph === "survey" ? null : "6 6", interactive: false,
      _ph: sg.ph, _sid: s.id }).addTo(future));
  }
  setLayer("routes", g);
  S.layers.future = future;
  setLayer("coverage", L.layerGroup());
  if (r.geo && r.geo.uncovered) setLayer("uncovered", L.geoJSON(r.geo.uncovered, { style: { color: "#475569", weight: 1, fillColor: "#0f172a", fillOpacity: 0.22, dashArray: "3 3" } }).bindPopup("Не покрыто (запретные зоны, буферы безопасности)"));
  drawChecks(r);
  drawExtraSites();
  applyRouteStyles();
  renderOverlayList();
}
function drawChecks(r) {
  const cg = L.layerGroup();
  for (const c of (r.validation?.checks || [])) for (const p of (c.points || [])) {
    const bad = !c.passed;
    L.marker([p.lat, p.lon], { zIndexOffset: 900, icon: L.divIcon({ className: "", iconSize: [24, 24], iconAnchor: [12, 12],
      html: `<div class="chk-ico" style="background:${bad ? "#dc2626" : "#f59e0b"};color:${bad ? "#dc2626" : "#f59e0b"}">${icon(bad ? "alert" : "target").replace('stroke="currentColor"', 'stroke="#fff"')}</div>` }) })
      .bindPopup(`<div class="pp-t">${esc(c.name)}</div><div>${esc(p.label)}</div><div class="hint" style="margin-top:4px">${hms(p.t)} · ${dm(p.t)}</div>`)
      .on("click", () => { if (p.sortie) selectSortie(p.sortie, false); setTime(p.t); })
      .addTo(cg);
  }
  if (cg.getLayers().length) setLayer("checks", cg);
}
function goToPoint(p) {
  if (!p) return;
  S.userMoved = true;
  map.flyTo([p.lat, p.lon], Math.max(map.getZoom(), 15), { duration: 0.6 });
  if (p.sortie) selectSortie(p.sortie, false, true);
  setTime(p.t);
  setTimeout(() => {
    S.layers.checks?.eachLayer((m) => { const ll = m.getLatLng(); if (Math.abs(ll.lat - p.lat) < 1e-6 && Math.abs(ll.lng - p.lon) < 1e-6) m.openPopup(); });
  }, 650);
}
function isDroneVisible(id) {
  if (S.hidden.has(id)) return false;
  if (S.routeFocus != null && S.plan?.sorties?.find(s => s.id === S.routeFocus)?.drone_id !== id) return false;
  const selected = S.plan?.sorties?.find((s) => s.id === S.sel);
  return !selected || S.showOtherDrones || selected.drone_id === id;
}
function styleFor(s, l) {
  const surv = l.options._ph === "survey";
  const hide = !isDroneVisible(s.drone_id) || (S.routeFocus != null && S.routeFocus !== s.id);
  const sel = S.sel === s.id, dim = S.sel !== null && !sel;
  return { color: routeColor(s, l.options._ph), opacity: hide ? 0 : dim ? 0.1 : (surv ? 0.88 : 0.6), weight: sel ? (surv ? 4 : 3) : (surv ? 3 : 2) };
}
function applyRouteStyles() {
  if (!S.plan || !S.plan.sorties) return;
  const routes = S.layers.routes;
  for (const s of S.plan.sorties) for (const l of S.routeLines[s.id] || []) {
    l.setStyle(styleFor(s, l));
    // Hidden routes must also be excluded from hit testing.
    if (isDroneVisible(s.drone_id) && (S.routeFocus == null || S.routeFocus === s.id)) { if (routes && !routes.hasLayer(l)) routes.addLayer(l); }
    else if (routes?.hasLayer(l)) routes.removeLayer(l);
  }
  for (const s of S.plan.sorties) for (const l of S.futureLines[s.id] || []) {
    l.setStyle({color: routeColor(s, l.options._ph), opacity: isDroneVisible(s.drone_id) && (S.routeFocus == null || S.routeFocus === s.id) ? (S.sel !== null && S.sel !== s.id ? 0.06 : 0.14) : 0});
  }
  if (S.sel !== null) (S.routeLines[S.sel] || []).forEach((l) => l.bringToFront());
}
function setFutureRoutes(on) {
  S.showFutureRoutes = on;
  const layer = S.layers.future;
  if (on && S.layers.routes && !map.hasLayer(S.layers.routes)) {
    S.layers.routes.addTo(map);
    store.set("off.routes", false);
  }
  if (layer) { if (on) layer.addTo(map); else map.removeLayer(layer); }
  renderOverlayList();
}
function updateRouteProgress() {
  for (const lines of Object.values(S.routeLines)) for (const l of lines) {
    if (!l._times) continue;
    const times = l._times, points = l._fullPts;
    let state, coords;
    if (S.t <= times[0]) { state = "future"; coords = []; }
    else if (S.t >= times[times.length - 1]) { state = "done"; coords = points; }
    else {
      state = S.t;
      let lo = 0, hi = times.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (times[mid] <= S.t) lo = mid; else hi = mid; }
      const f = (S.t - times[lo]) / (times[hi] - times[lo]);
      coords = points.slice(0, lo + 1).concat([[points[lo][0] + (points[hi][0] - points[lo][0]) * f,
                                                 points[lo][1] + (points[hi][1] - points[lo][1]) * f]]);
    }
    if (l._progressState !== state) { l.setLatLngs(coords); l._progressState = state; }
  }
}
function hoverSortie(id, on) {
  if (S.sel === id) return;
  const s = S.plan.sorties.find((x) => x.id === id);
  for (const l of S.routeLines[id] || []) {
    if (on && isDroneVisible(s.drone_id)) { l.setStyle({ weight: l.options._ph === "survey" ? 3.4 : 2.4, opacity: 1 }); l.bringToFront(); } else l.setStyle(styleFor(s, l));
  }
}
function drawSelection(s) {
  dropLayer("sel");
  if (!s) return;
  const g = L.layerGroup();
  for (const kp of s.keypoints || []) {
    const w = s.waypoints[kp.i];
    L.marker([w[1], w[0]], { icon: L.divIcon({ className: "", iconSize: [10, 10], iconAnchor: [5, 5], html: `<div class="kp-ico" style="border-color:${S.colors[s.drone_id]}"></div>` }) })
      .bindTooltip(`${esc(kp.label)} · ${hm(w[4])} · ${Math.round(w[3])} м`, { direction: "top", offset: [0, -6] }).addTo(g);
  }
  S.layers.sel = g; g.addTo(map);
}
function selectSortie(id, zoom, keepView) {
  const s = S.plan?.sorties?.find((x) => x.id === id);
  if (!s) return;
  if (S.routeFocus != null && S.routeFocus !== id) { S.routeFocus = id; $("#route-focus").value = String(id); }
  S.sel = id;
  S.showOtherDrones = false;
  S.hidden.delete(s.drone_id);
  renderDrones();
  applyRouteStyles();
  drawSelection(s);
  $$("#sorties tr.row").forEach((tr) => tr.classList.toggle("sel", +tr.dataset.s === id));
  if (zoom && !keepView) { S.userMoved = true; map.fitBounds(L.latLngBounds(s.waypoints.map((w) => [w[1], w[0]])), { paddingTopLeft: [40, 40], paddingBottomRight: [40, 40], maxZoom: 16 }); }
  setTime(S.t >= s.t_takeoff && S.t <= s.t_landing ? S.t : s.t_takeoff);
  renderInspector(s);
  drawGantt();
}
function clearSelection() {
  if (S.sel === null) return;
  S.sel = null; S.showOtherDrones = false; applyRouteStyles(); setTime(S.t); dropLayer("sel"); drawGantt(); $("#inspector").hidden = true;
  $$("#sorties tr.row").forEach((tr) => tr.classList.remove("sel"));
}
map.on("click", (e) => {
  if (S.addSite) {
    const n = S.extraSites.length + 1;
    S.extraSites.push({ lon: +e.latlng.lng.toFixed(6), lat: +e.latlng.lat.toFixed(6), name: siteName("Площадка"), manual: true });
    invalidatePlan();
    drawScenario(S.scenario);
    toast(`Площадка ${n} добавлена. Еще клик — следующая; Esc — готово`);
    return;
  }
  clearSelection();
});
function setAddSite(on) {
  S.addSite = on;
  $("#add-site").classList.toggle("on", on);
  $("#add-site").setAttribute("aria-pressed", String(on));
  document.body.classList.toggle("adding-site", on);
  if (on) toast("Кликните по карте, чтобы поставить площадку взлета/посадки. Esc — закончить");
}
$("#add-site").addEventListener("click", (e) => { e.stopPropagation(); setAddSite(!S.addSite); });
function stepSortie(d) {
  if (!S.plan?.sorties?.length) return;
  const list = S.plan.sorties.filter((x) => !S.hidden.has(x.drone_id));
  const i = S.sel === null ? (d > 0 ? -1 : list.length) : list.findIndex((x) => x.id === S.sel);
  const n = list[Math.max(0, Math.min(list.length - 1, i + d))];
  if (n) selectSortie(n.id, true);
}

// ---------------------------------------------------------------- инспектор вылета
function renderInspector(s) {
  const el = $("#inspector");
  el.hidden = false;
  const col = S.colors[s.drone_id];
  const total = s.t_landing - s.t_takeoff;
  const ph = (s.phases || []).filter((p) => p.phase !== "ground" && p.t_end > p.t_start);
  const byPh = {};
  ph.forEach((p) => { byPh[p.phase] = (byPh[p.phase] || 0) + (p.t_end - p.t_start); });
  const surveySeconds = byPh.survey || 0;
  const surveyShare = total > 0 ? surveySeconds / total * 100 : 0;
  const surveyDistanceShare = s.distance_km > 0 ? s.survey_km / s.distance_km * 100 : 0;
  const avgSpeed = total > 0 ? s.distance_km / total * 3600 : 0;
  const current = posAt(s, S.t);
  const pcol = (p) => p === "survey" ? col : PHASE_COLORS[p];
  const plane = s.model === "geoscan_201";
  el.innerHTML = `
    <div class="ins-h"><span class="sw" style="background:${col};width:12px;height:12px"></span>
      <div><div class="ins-t">Вылет ${s.id} · ${esc(s.drone_id)}</div></div>
      <div class="ins-nav"><button class="eye" id="ins-prev" title="Предыдущий вылет (←)">${icon("chevL")}</button><button class="eye" id="ins-next" title="Следующий вылет (→)">${icon("chevR")}</button><button class="eye" id="ins-x" title="Закрыть (Esc)">${icon("x")}</button></div></div>
    <div class="ins-sub">${esc(s.model_title)} · ${dm(s.t_takeoff)} ${dur(s.takeoff_iso, s.landing_iso)} · ${esc(s.site)}${s.landing_site !== s.site ? ` → ${esc(s.landing_site)}` : ""}</div>
    <div class="ins-k">
      <div><b>${fmt(s.duration_min, 0)} мин</b><span>в воздухе</span></div>
      <div title="Расход АКБ; предел 80% — резерв 20%"><b>${fmt(s.battery_used_pct, 0)}%</b><span>расход АКБ</span><div class="bat"><i style="width:${Math.min(100, s.battery_used_pct)}%;background:${s.battery_used_pct > 80 ? "var(--bad)" : "var(--ok)"}"></i><em></em></div></div>
      <div title="Путь, из него съемка ${fmt(s.survey_km, 1)} км"><b>${fmt(s.distance_km, 1)} км</b><span>съемка ${fmt(s.survey_km, 1)}</span></div>
      <div title="Высота съемки над землей; эшелон перелета ${fmt(s.transit_alt_agl_m, 0)} м"><b>${fmt(s.survey_alt_agl_m, 0)} м</b><span>эш. ${fmt(s.transit_alt_agl_m, 0)} м</span></div>
    </div>
    <div class="phasebar" title="Этапы полета">${ph.map((p) => `<i style="width:${(p.t_end - p.t_start) / total * 100}%;background:${pcol(p.phase)}" title="${PHASE_RU[p.phase]}: ${hmDur(p.t_end - p.t_start)}"></i>`).join("")}</div>
    <div class="phase-l">${Object.entries(byPh).map(([p, t]) => `<span style="--c:${pcol(p)}">${PHASE_RU[p]} ${hmDur(t)}</span>`).join("")}</div>
    <div class="ins-analysis"><strong>Аналитика следа</strong><div class="ins-analysis-grid">
      <div><b>${fmt(surveyShare, 0)}%</b><span>времени на съёмку</span></div>
      <div><b>${fmt(surveyDistanceShare, 0)}%</b><span>пути на съёмку</span></div>
      <div><b>${fmt(avgSpeed, 1)} км/ч</b><span>средняя скорость</span></div>
      <div><b>${s.waypoints.length}</b><span>точек маршрута</span></div>
    </div><div class="ins-current" id="ins-current">${current ? `${hm(S.t)} · ${esc(PHASE_RU[current.ph] || current.ph)} · ${fmt(current.agl, 0)} м над землёй` : "Борт вне маршрута в выбранное время"}</div></div>
    <div class="prof" id="ins-prof">${profileSVG(s)}<div class="prof-tip" id="prof-tip" hidden></div></div>
    <div class="ins-a">
      <button class="btn btn-sm" id="ins-play">${ico("play")}Проиграть</button>
      ${canExport(S.plan) ? `<a class="btn btn-sm" href="/api/plans/${S.plan.id}/export/qgc?sortie=${s.id}" title="Полетное задание вылета для QGroundControl">${ico("download")}.plan</a>
      <a class="btn btn-sm" href="/api/plans/${S.plan.id}/brief?drone=${encodeURIComponent(s.drone_id)}" target="_blank" rel="noopener">${ico("printer")}Задание борта</a>` : ""}
      ${s.ground_hold_s ? `<span class="hint" style="align-self:center">удержание ${s.ground_hold_s} с — разведение</span>` : ""}
      ${plane && s.launch_heading_deg !== null ? `<span class="hint" style="align-self:center">запуск курсом ${s.launch_heading_deg}°</span>` : ""}
    </div>`;
  $("#ins-x").onclick = clearSelection;
  $("#ins-prev").onclick = () => stepSortie(-1);
  $("#ins-next").onclick = () => stepSortie(1);
  $("#ins-play").onclick = () => { setTime(s.t_takeoff); if (!S.playing) togglePlay(); };
  bindProfile(s);
}
function profileSVG(s) {
  const W = 344, H = 118, l = 34, r = 4, t = 6, b = 16;
  const Wp = s.waypoints, t0 = Wp[0][4], T = Wp[Wp.length - 1][4] - t0 || 1;
  const Z = Wp.map((w) => w[2]), G = Wp.map((w) => w[2] - w[3]);
  let z0 = Math.min(...G) - 10, z1 = Math.max(...Z) + 20;
  const ceil = S.plan.summary.ceiling_agl_m;
  const cz = ceil ? G.map((g) => g + ceil) : null;
  if (cz && Math.min(...cz) < z1 + 80) z1 = Math.max(z1, Math.max(...cz) + 8);
  const X = (v) => l + (W - l - r) * (v - t0) / T, Y = (v) => t + (H - t - b) * (1 - (v - z0) / (z1 - z0));
  const gnd = Wp.map((w, i) => `${X(w[4]).toFixed(1)},${Y(G[i]).toFixed(1)}`).join(" ");
  const col = S.colors[s.drone_id];
  let lines = "", cur = null, pts = [];
  const flush = () => { if (pts.length > 1) lines += `<polyline points="${pts.join(" ")}" fill="none" stroke="${cur === "survey" ? col : "#64748b"}" stroke-width="${cur === "survey" ? 2 : 1.4}" ${cur === "survey" ? "" : 'stroke-dasharray="4 2"'} stroke-linejoin="round"/>`; };
  for (let i = 0; i < Wp.length; i++) {
    const ph = Wp[i][5] === "survey" ? "survey" : "other";
    const p = `${X(Wp[i][4]).toFixed(1)},${Y(Z[i]).toFixed(1)}`;
    if (cur !== ph) { if (pts.length) { flush(); pts = [pts[pts.length - 1]]; } cur = ph; }
    pts.push(p);
  }
  flush();
  const mins = T / 60, step = [1, 2, 5, 10, 15, 20, 30, 60].find((k) => mins / k <= 5) || 60;
  let ax = "";
  for (let m = 0; m <= mins + 1e-6; m += step) ax += `<text class="ax" x="${X(t0 + m * 60)}" y="${H - 3}" text-anchor="middle">${m}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" id="prof-svg">
    <line class="grid" x1="${l}" x2="${W - r}" y1="${Y(z1)}" y2="${Y(z1)}"/><line class="grid" x1="${l}" x2="${W - r}" y1="${Y(z0)}" y2="${Y(z0)}"/>
    <text class="ax" x="${l - 3}" y="${Y(z1) + 3}" text-anchor="end">${Math.round(z1)}</text><text class="ax" x="${l - 3}" y="${Y(z0) + 3}" text-anchor="end">${Math.round(z0)}</text>
    <polygon class="gnd" points="${X(t0)},${Y(z0)} ${gnd} ${X(t0 + T)},${Y(z0)}"/>
    ${cz && Math.min(...cz) < z1 ? `<polyline class="ceil" points="${Wp.map((w, i) => `${X(w[4]).toFixed(1)},${Y(cz[i]).toFixed(1)}`).join(" ")}"/>` : ""}
    ${lines}${ax}<line class="cur" id="prof-cur" x1="-10" x2="-10" y1="${t}" y2="${H - b}"/></svg>`;
}
function bindProfile(s) {
  const svg = $("#prof-svg"); if (!svg) return;
  const W = 344, l = 34, r = 4, t0 = s.waypoints[0][4], T = s.waypoints[s.waypoints.length - 1][4] - t0 || 1;
  const at = (e) => { const rc = svg.getBoundingClientRect(); const x = (e.clientX - rc.left) / rc.width * W; return t0 + Math.max(0, Math.min(1, (x - l) / (W - l - r))) * T; };
  let drag = false;
  svg.addEventListener("pointerdown", (e) => { drag = true; svg.setPointerCapture(e.pointerId); if (S.playing) togglePlay(); setTime(at(e)); });
  svg.addEventListener("pointermove", (e) => {
    const t = at(e);
    const p = posAt(s, t);
    const tip = $("#prof-tip");
    if (p) { tip.hidden = false; tip.textContent = `${hm(t)} · ${Math.round(p.agl)} м над землей · ${PHASE_RU[p.ph] || p.ph}`; }
    if (drag) setTime(t);
  });
  svg.addEventListener("pointerup", () => { drag = false; });
  svg.addEventListener("pointerleave", () => { $("#prof-tip").hidden = true; });
}
function updateProfileCursor() {
  const s = S.sel !== null && S.plan?.sorties?.find((x) => x.id === S.sel);
  const c = $("#prof-cur"); if (!s || !c) return;
  const W = 344, l = 34, r = 4, t0 = s.waypoints[0][4], T = s.waypoints[s.waypoints.length - 1][4] - t0 || 1;
  const x = S.t >= t0 && S.t <= t0 + T ? l + (W - l - r) * (S.t - t0) / T : -10;
  c.setAttribute("x1", x); c.setAttribute("x2", x);
}

// ================================================================ время и анимация
function setupTimeline(r) {
  if (!r.sorties || !r.sorties.length) { $("#dock").hidden = true; return; }
  $("#dock").hidden = false;
  $("#timeline-drone").innerHTML = '<option value="">Все БПЛА</option>' + r.drones.filter(d => d.sorties.length)
    .map(d => `<option value="${esc(d.id)}">${esc(d.id)}</option>`).join("");
  $("#timeline-drone").value = S.timelineDrone;
  S.t0 = Math.min(...r.sorties.map((s) => s.t_takeoff)) - 300;
  S.t1 = Math.max(...r.sorties.map((s) => s.t_landing)) + 300;
  renderTimelineEvents(r);
  S.markers = {};
  const g = L.layerGroup();
  r.drones.forEach((d) => {
    S.markers[d.id] = L.marker([0, 0], { zIndexOffset: 1000, icon: L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11],
      html: `<div class="drone-icon" style="background:${S.colors[d.id]}">${d.model === "geoscan_201" ? "✈" : d.id.split("-")[1]}</div>` }) })
      .bindTooltip(d.id, { direction: "top", offset: [0, -10] })
      .on("click", () => { const s = r.sorties.find((x) => x.drone_id === d.id && x.t_takeoff <= S.t && S.t <= x.t_landing); if (s) selectSortie(s.id, false); });
  });
  setLayer("drones", g);
  setTime(S.t0);
  drawGantt();
  setTimeout(() => { map.invalidateSize(); if (!S.userMoved) fitScenario(); }, 60);
}
async function loadCoverage(plan) {
  try {
    const progress = await api(`/api/plans/${encodeURIComponent(plan.id)}/progress`);
    if (S.plan !== plan) return;
    S.progress = progress; S.coverageShown = 0;
    updateCoverage();
  } catch (e) {
    if (S.plan === plan) {
      $("#coverage-percent").textContent = "Покрытие по времени недоступно";
      $("#coverage-area").textContent = "След маршрута можно просмотреть по шкале времени";
    }
  }
}
function showCoverageAnalytics(step, index, latlng) {
  if (!S.progress) return;
  stopPlayback();
  clearSelection();
  setTime(step.t);
  const previous = S.progress.steps[index - 1];
  const gained = step.covered_km2 == null ? null : Math.max(0, step.covered_km2 - (previous?.covered_km2 || 0));
  const area = S.progress.allowed_km2;
  const content = `<div class="coverage-popup"><div class="pp-t">След съёмки · ${dm(step.t)} ${hm(step.t)}</div>
    <div class="coverage-popup-grid"><div><b>${fmt(step.pct)}%</b><span>покрыто к этому времени</span></div>
    <div><b>${fmt(step.covered_km2, 2)} км²</b><span>из ${fmt(area, 2)} км² разрешённой площади</span></div>
    <div><b>${fmt(gained, 3)} км²</b><span>новый участок на этом шаге</span></div>
    <div><b>${hm(step.from)}–${hm(step.t)}</b><span>интервал съёмки</span></div></div>
    <div class="hint">Расчёт по плану всей миссии; фактическая телеметрия не учитывается.</div></div>`;
  L.popup({maxWidth: 310}).setLatLng(latlng).setContent(content).openOn(map);
}
function updateCoverage() {
  if (!S.progress) return;
  const steps = S.progress.steps || [], layer = S.layers.coverage;
  while (S.coverageShown < steps.length && steps[S.coverageShown].t <= S.t) {
    const step = steps[S.coverageShown++], index = S.coverageShown - 1;
    if (!step._layer) step._layer = L.geoJSON(step.geometry, {
      renderer: coverageR,
      style: {stroke: false, fillColor: "#16a34a", fillOpacity: 0.23},
      interactive: true, bubblingMouseEvents: false
    });
    if (!step._clickBound) {
      step._layer.on("click", e => { if (e.originalEvent) L.DomEvent.stopPropagation(e.originalEvent); showCoverageAnalytics(step, index, e.latlng); });
      step._clickBound = true;
    }
    layer?.addLayer(step._layer);
  }
  while (S.coverageShown > 0 && steps[S.coverageShown - 1].t > S.t) {
    layer?.removeLayer(steps[--S.coverageShown]._layer);
  }
  const previous = steps[S.coverageShown - 1], next = steps[S.coverageShown];
  let pct = previous?.pct ?? 0;
  if (next && next.pct !== null && S.t > next.from && next.t > next.from) {
    pct += (next.pct - pct) * Math.min(1, (S.t - next.from) / (next.t - next.from));
  }
  if (!steps.length || S.progress.final_pct === null) {
    $("#coverage-percent").textContent = "Нет данных о покрытии по времени";
    $("#coverage-area").textContent = "";
    return;
  }
  $("#coverage-percent").textContent = `Вся миссия по плану ≈ ${fmt(pct, 1)}%`;
  $("#coverage-area").textContent = S.progress.allowed_km2 == null ? "От разрешённой площади" :
    `≈ ${fmt(pct * S.progress.allowed_km2 / 100, 2)} из ${fmt(S.progress.allowed_km2, 2)} км² разрешённой площади`;
}
function prepareObstacleGeometry(geometry) {
  const raw = geometry?.type === "Point" ? [[geometry.coordinates]] :
    geometry?.type === "LineString" ? [geometry.coordinates] :
    geometry?.type === "MultiLineString" ? geometry.coordinates :
    geometry?.type === "Polygon" ? geometry.coordinates :
    geometry?.type === "MultiPolygon" ? geometry.coordinates.flat() : [];
  const lines = raw.filter(line => Array.isArray(line) && line.length && line.every(p => Number.isFinite(p?.[0]) && Number.isFinite(p?.[1])));
  if (!lines.length) return null;
  const lat = lines[0][0][1] * Math.PI / 180, xscale = 111320 * Math.cos(lat), yscale = 111320;
  const bbox = {minLon: Infinity, minLat: Infinity, maxLon: -Infinity, maxLat: -Infinity};
  const edges = [];
  for (const line of lines) {
    for (const p of line) {
      bbox.minLon = Math.min(bbox.minLon, p[0]); bbox.maxLon = Math.max(bbox.maxLon, p[0]);
      bbox.minLat = Math.min(bbox.minLat, p[1]); bbox.maxLat = Math.max(bbox.maxLat, p[1]);
    }
    for (let j = 0; j < line.length; j++) {
      const c = [line[j][0] * xscale, line[j][1] * yscale];
      const q = line[j + 1] || line[j], d = [q[0] * xscale, q[1] * yscale];
      edges.push({c, d, minx: Math.min(c[0], d[0]), maxx: Math.max(c[0], d[0]),
        miny: Math.min(c[1], d[1]), maxy: Math.max(c[1], d[1])});
    }
  }
  return {bbox, edges, xscale, yscale};
}
function sortieRouteBounds(sortie) {
  const W = sortie.waypoints || [];
  const b = {minLon: Infinity, minLat: Infinity, maxLon: -Infinity, maxLat: -Infinity};
  for (const p of W) {
    b.minLon = Math.min(b.minLon, p[0]); b.maxLon = Math.max(b.maxLon, p[0]);
    b.minLat = Math.min(b.minLat, p[1]); b.maxLat = Math.max(b.maxLat, p[1]);
  }
  return b;
}
function nearestObstacleOnSortie(sortie, geometry, routeBounds) {
  const obstacle = geometry?.edges ? geometry : prepareObstacleGeometry(geometry);
  const W = sortie.waypoints || [];
  if (!obstacle || W.length < 2) return null;
  const {bbox, edges, xscale, yscale} = obstacle;
  const b = routeBounds || sortieRouteBounds(sortie);
  const lonPad = 250 / Math.max(xscale, 1), latPad = 250 / yscale;
  if (b.maxLon < bbox.minLon - lonPad || b.minLon > bbox.maxLon + lonPad ||
      b.maxLat < bbox.minLat - latPad || b.minLat > bbox.maxLat + latPad) return null;
  const xy = p => [p[0] * xscale, p[1] * yscale];
  const closest = (p, a, b) => {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const u = dx || dy ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy))) : 0;
    return {u, d2: (p[0] - a[0] - u * dx) ** 2 + (p[1] - a[1] - u * dy) ** 2};
  };
  let best = null;
  for (let i = 1; i < W.length; i++) {
    if (W[i - 1][5] === "ground" && W[i][5] === "ground") continue;
    const a = xy(W[i - 1]), b = xy(W[i]);
    const minx = Math.min(a[0], b[0]), maxx = Math.max(a[0], b[0]);
    const miny = Math.min(a[1], b[1]), maxy = Math.max(a[1], b[1]);
    if (maxx < bbox.minLon * xscale - 250 || minx > bbox.maxLon * xscale + 250 ||
        maxy < bbox.minLat * yscale - 250 || miny > bbox.maxLat * yscale + 250) continue;
    for (const edge of edges) {
      if (maxx < edge.minx - 250 || minx > edge.maxx + 250 ||
          maxy < edge.miny - 250 || miny > edge.maxy + 250) continue;
      const {c, d} = edge;
      const candidates = [closest(c, a, b), closest(d, a, b),
        {...closest(a, c, d), u: 0}, {...closest(b, c, d), u: 1}];
      const rx = b[0] - a[0], ry = b[1] - a[1], ox = d[0] - c[0], oy = d[1] - c[1];
      const cross = rx * oy - ry * ox;
      if (Math.abs(cross) > 1e-9) {
        const qx = c[0] - a[0], qy = c[1] - a[1];
        const u = (qx * oy - qy * ox) / cross, v = (qx * ry - qy * rx) / cross;
        if (u >= 0 && u <= 1 && v >= 0 && v <= 1) candidates.push({u, d2: 0});
      }
      for (const item of candidates) if (!best || item.d2 < best.d2) {
        best = {d2: item.d2, t: W[i - 1][4] + item.u * (W[i][4] - W[i - 1][4])};
      }
    }
  }
  return best && {t: best.t, distance_m: Math.sqrt(best.d2)};
}
function collectTimelineEvents(plan, scenario, t0, t1) {
  const events = [];
  const routeBounds = new Map((plan.sorties || []).map(sortie => [sortie, sortieRouteBounds(sortie)]));
  const obstaclesBySortie = new Map();
  const add = (t, kind, label, droneId = "") => { if (Number.isFinite(t) && t >= t0 && t <= t1) events.push({t, kind, label, droneId}); };
  for (const s of plan.sorties || []) {
    add(s.t_takeoff, "flight", `Вылет ${s.id}, ${s.drone_id}: взлёт`, s.drone_id);
    add(s.t_landing, "flight", `Вылет ${s.id}, ${s.drone_id}: посадка`, s.drone_id);
    const surveys = (s.phases || []).filter(p => p.phase === "survey");
    if (surveys.length) {
      add(Math.min(...surveys.map(p => p.t_start)), "survey", `Вылет ${s.id}: начало съёмки`, s.drone_id);
      add(Math.max(...surveys.map(p => p.t_end)), "survey", `Вылет ${s.id}: завершение съёмки`, s.drone_id);
    }
  }
  for (const check of plan.validation?.checks || []) for (const p of check.points || []) {
    const prefix = check.passed ? "Контроль" : "Нарушение";
    const droneId = (plan.sorties || []).find(s => s.id === Number(p.sortie))?.drone_id || "";
    add(Number(p.t), check.passed ? "check" : "alert", `${prefix}: ${p.label || check.name}`, droneId);
  }
  let workBounds = null;
  try {
    if (plan.geo?.requested) workBounds = L.geoJSON(plan.geo.requested).getBounds();
    for (const sortie of plan.sorties || []) for (const p of sortie.waypoints || []) {
      if (workBounds) workBounds.extend([p[1], p[0]]);
    }
  } catch (e) { workBounds = null; }
  for (const f of scenario?.features || []) {
    const prop = f.properties || {};
    if (prop.kind === "obstacle") {
      const obstacle = prepareObstacleGeometry(f.geometry);
      if (!obstacle) continue;
      for (const sortie of plan.sorties || []) {
        const near = nearestObstacleOnSortie(sortie, obstacle, routeBounds.get(sortie));
        if (near && near.distance_m <= 250) {
          if (!obstaclesBySortie.has(sortie)) obstaclesBySortie.set(sortie, []);
          obstaclesBySortie.get(sortie).push({...near, name: prop.name || "без названия"});
        }
      }
      continue;
    }
    if (prop.kind !== "no_fly" || (!prop.active_from && !prop.active_to)) continue;
    try { if (workBounds && !L.geoJSON(f.geometry).getBounds().intersects(workBounds)) continue; } catch (e) { continue; }
    const name = prop.name || "Временная зона";
    if (prop.active_from) add(Date.parse(prop.active_from) / 1000, "restriction", `Начало действия зоны «${name}»`);
    if (prop.active_to) add(Date.parse(prop.active_to) / 1000, "restriction", `Окончание действия зоны «${name}»`);
  }
  for (const [sortie, near] of obstaclesBySortie) {
    const picked = [];
    for (const item of near.sort((a, b) => a.distance_m - b.distance_m)) {
      if (picked.every(other => Math.abs(other.t - item.t) >= 120)) picked.push(item);
      if (picked.length === 3) break;
    }
    for (const item of picked) add(item.t, "obstacle",
      `Вылет ${sortie.id}: рядом с препятствием «${item.name}» (≈ ${Math.round(item.distance_m)} м по горизонтали)`, sortie.drone_id);
  }
  return events.sort((a, b) => a.t - b.t).map((event, index) => ({...event, index}));
}
function groupTimelineEvents(events, t0, t1, slots = 32) {
  const groups = new Map();
  for (const event of events) {
    const slot = Math.min(slots - 1, Math.floor((event.t - t0) / Math.max(t1 - t0, 1) * slots));
    if (!groups.has(slot)) groups.set(slot, []);
    groups.get(slot).push(event);
  }
  const rank = {alert: 6, obstacle: 5, restriction: 4, check: 3, survey: 2, flight: 1};
  return [...groups.values()].map(items => ({events: items,
    t: items.reduce((sum, e) => sum + e.t, 0) / items.length,
    kind: items.reduce((best, e) => rank[e.kind] > rank[best] ? e.kind : best, "flight")}));
}
function renderTimelineEvents(plan) {
  S.timelineEvents = collectTimelineEvents(plan, S.scenario, S.t0, S.t1);
  const visibleEvents = S.timelineDrone ? S.timelineEvents.filter(event => event.droneId === S.timelineDrone) : S.timelineEvents;
  const slots = Math.max(8, Math.min(32, Math.floor(($("#timeline-markers").clientWidth || 800) / 26)));
  S.timelineGroups = groupTimelineEvents(visibleEvents, S.t0, S.t1, slots);
  $("#timeline-markers").innerHTML = S.timelineGroups.map((group, index) => {
    const pct = Math.max(0, Math.min(100, (group.t - S.t0) / (S.t1 - S.t0) * 100));
    const label = group.events.length === 1 ? group.events[0].label : `${group.events.length} ${plural(group.events.length, "событие", "события", "событий")}`;
    const time = group.events.length === 1 ? `${dm(group.t)} ${hm(group.t)}` :
      `${dm(group.events[0].t)} ${hm(group.events[0].t)} — ${dm(group.events[group.events.length - 1].t)} ${hm(group.events[group.events.length - 1].t)}`;
    return `<button class="timeline-marker event-${group.kind}" type="button" style="left:${pct}%" data-group="${index}"
      aria-label="${esc(time)}: ${esc(label)}" title="${esc(time)} · ${esc(label)}. Нажмите для подробностей"></button>`;
  }).join("");
  $$("#timeline-markers button").forEach(button => button.addEventListener("click", () => showTimelineGroup(+button.dataset.group)));
}
function showTimelineGroup(index) {
  const group = S.timelineGroups[index]; if (!group) return;
  const pop = $("#timeline-event-popover");
  pop.hidden = false;
  pop.innerHTML = `<strong>${group.events.length === 1 ? "Событие" : `${group.events.length} ${plural(group.events.length, "событие", "события", "событий")} по плану`}</strong>` +
    group.events.map(event => `<button type="button" data-event="${event.index}"><span>${esc(dm(event.t))} ${esc(hm(event.t))}</span>${esc(event.label)}</button>`).join("");
  $$("button[data-event]", pop).forEach(button => button.addEventListener("click", () => {
    const event = S.timelineEvents[+button.dataset.event];
    S.highlightedEvent = event;
    setTime(event.t); pop.hidden = true;
  }));
}
function updateTimelineCaption() {
  const events = S.timelineDrone ? S.timelineEvents.filter(event => event.droneId === S.timelineDrone) : S.timelineEvents;
  const current = S.highlightedEvent;
  const atTime = events.find(event => Math.abs(event.t - S.t) < 1);
  const next = events.find(event => event.t > S.t);
  const active = current && Math.abs(current.t - S.t) < 30 ? current : atTime;
  const label = active ? `Сейчас ${dm(active.t)} ${hm(active.t)} · ${active.label}` :
    next ? `Далее ${dm(next.t)} ${hm(next.t)} · ${next.label}` : "События миссии завершены";
  if ($("#timeline-next").textContent !== label) {
    $("#timeline-next").textContent = label;
    $("#timeline-next").title = label;
  }
  for (let i = 0; i < S.timelineGroups.length; i++) {
    $("#timeline-markers").children[i]?.classList.toggle("past", S.timelineGroups[i].t < S.t);
  }
}
function posAt(s, t) {
  const W = s.waypoints;
  if (t < W[0][4] || t > W[W.length - 1][4]) return null;
  let lo = 0, hi = W.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (W[m][4] <= t) lo = m; else hi = m; }
  const a = W[lo], b = W[hi], f = b[4] > a[4] ? (t - a[4]) / (b[4] - a[4]) : 0;
  return { lat: a[1] + (b[1] - a[1]) * f, lon: a[0] + (b[0] - a[0]) * f, agl: a[3] + (b[3] - a[3]) * f, ph: b[5] };
}
function setTime(t) {
  if (!S.plan || !S.plan.sorties) return;
  S.t = Math.max(S.t0, Math.min(S.t1, t));
  updateRouteProgress();
  updateCoverage();
  $("#slider").value = Math.round(1000 * (S.t - S.t0) / (S.t1 - S.t0));
  $("#clock-t").textContent = hms(S.t);
  $("#clock-d").textContent = dm(S.t);
  updateTimelineCaption();
  const g = S.layers.drones;
  let air = 0;
  for (const d of S.plan.drones) {
    const mk = S.markers[d.id];
    const s = S.plan.sorties.find((x) => x.drone_id === d.id && x.t_takeoff <= S.t && S.t <= x.t_landing);
    const p = s && isDroneVisible(d.id) && posAt(s, S.t);
    if (p) {
      air++;
      mk.setLatLng([p.lat, p.lon]);
      mk.setTooltipContent(`${d.id} · ${PHASE_RU[p.ph] || p.ph} · ${Math.round(p.agl)} м`);
      if (g && !g.hasLayer(mk)) g.addLayer(mk);
    } else if (g && g.hasLayer(mk)) g.removeLayer(mk);
  }
  $("#dock-live").innerHTML = `<span class="live ${air ? "on" : ""}"></span>${S.sel !== null && !S.showOtherDrones ? "на карте в воздухе" : "в воздухе"}: <b>${air}</b>`;
  const cur = $("#gantt .g-cursor");
  if (cur && S.gx) { const x = S.gx(S.t); cur.setAttribute("x1", x); cur.setAttribute("x2", x); }
  updateProfileCursor();
  const current = $("#ins-current");
  if (current && S.sel !== null) {
    const sortie = S.plan.sorties.find(s => s.id === S.sel), position = sortie && posAt(sortie, S.t);
    current.textContent = position ? `${hm(S.t)} · ${PHASE_RU[position.ph] || position.ph} · ${fmt(position.agl, 0)} м над землёй` : "Борт вне маршрута в выбранное время";
  }
}
$("#slider").addEventListener("input", (e) => setTime(S.t0 + (S.t1 - S.t0) * (+e.target.value) / 1000));
chips("#speed", (v) => { S.speed = +v; });
function togglePlay() {
  if (!S.plan || !S.plan.sorties) return;
  if (S.playing) { stopPlayback(); return; }
  const plan = S.plan;
  S.playing = true;
  $("#play").innerHTML = ico(S.playing ? "pause" : "play");
  if (S.playing && S.t >= S.t1 - 1) setTime(S.t0);
  let last = performance.now();
  const step = (now) => {
    if (!S.playing || S.plan !== plan) return;
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    let t = S.t + dt * S.speed;
    // пропуск ночей и длинных пауз — к следующему взлету
    if (!S.plan.sorties.some((s) => s.t_takeoff <= t && t <= s.t_landing)) {
      const nxt = S.plan.sorties.map((s) => s.t_takeoff).filter((x) => x > t).sort((a, b) => a - b)[0];
      if (nxt && nxt - t > 1800) t = nxt - 60;
    }
    if (t >= S.t1) { t = S.t1; S.playing = false; $("#play").innerHTML = ico("play"); }
    setTime(t);
    S.animationFrame = S.playing ? requestAnimationFrame(step) : null;
  };
  S.animationFrame = requestAnimationFrame(step);
}
function stopPlayback() {
  S.playing = false;
  if (S.animationFrame !== null) cancelAnimationFrame(S.animationFrame);
  S.animationFrame = null;
  $("#play").innerHTML = ico("play");
}
$("#play").addEventListener("click", togglePlay);
$("#timeline-drone").addEventListener("change", e => {
  S.timelineDrone = e.target.value;
  if (S.plan) { renderTimelineEvents(S.plan); updateTimelineCaption(); drawGantt(); }
});
function setTimelineExpanded(expanded) {
  const dock = $("#dock"), button = $("#dock-expand");
  dock.classList.toggle("expanded", expanded);
  if (expanded) dock.classList.remove("collapsed");
  button.setAttribute("aria-pressed", String(expanded));
  button.title = expanded ? "Вернуть обычную высоту таймлайна" : "Развернуть таймлайн на всю высоту";
  button.setAttribute("aria-label", button.title);
  requestAnimationFrame(() => { drawGantt(); map.invalidateSize(); });
}
$("#dock-expand").addEventListener("click", () => setTimelineExpanded(!$("#dock").classList.contains("expanded")));
document.addEventListener("click", e => { if (!e.target.closest(".timeline-control")) $("#timeline-event-popover").hidden = true; });
document.addEventListener("keydown", e => { if (e.key === "Escape") { $("#timeline-event-popover").hidden = true; if ($("#dock").classList.contains("expanded")) setTimelineExpanded(false); } });
new ResizeObserver(() => {
  if (S.plan && !$("#dock").hidden) { renderTimelineEvents(S.plan); updateTimelineCaption(); }
}).observe($(".timeline-track"));
$("#dock-toggle").addEventListener("click", () => { if ($("#dock").classList.contains("expanded")) setTimelineExpanded(false); $("#dock").classList.toggle("collapsed"); store.set("dock.collapsed", $("#dock").classList.contains("collapsed")); setTimeout(() => map.invalidateSize(), 50); });

function drawGantt() {
  const r = S.plan;
  if (!r || !r.sorties || !r.sorties.length) return;
  const box = $("#gantt");
  const drones = S.timelineDrone ? r.drones.filter(d => d.id === S.timelineDrone) : r.drones;
  const W = Math.max(640, box.clientWidth - 26), rowH = $("#dock").classList.contains("expanded") ? Math.min(72, Math.max(30, Math.floor((box.clientHeight - 36) / Math.max(drones.length, 1)))) : 22, left = 104, right = 56, top = 22;
  const segs = [];
  for (const [a, b] of r.sorties.map((s) => [s.t_takeoff, s.t_landing]).sort((x, y) => x[0] - y[0])) {
    if (segs.length && a - segs[segs.length - 1][1] < 3 * 3600) segs[segs.length - 1][1] = Math.max(segs[segs.length - 1][1], b);
    else segs.push([a, b]);
  }
  segs[0][0] -= 600; segs[segs.length - 1][1] += 600;
  const gap = 26, total = segs.reduce((acc, [a, b]) => acc + (b - a), 0);
  const scale = (W - left - right - gap * (segs.length - 1)) / total;
  const gx = (t) => { let x = left; for (const [a, b] of segs) { if (t <= b) return x + Math.max(0, t - a) * scale; x += (b - a) * scale + gap; } return x - gap; };
  S.gx = gx;
  const H = top + drones.length * rowH + 4;
  let svg = `<svg width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg">`;
  const hourStep = scale * 3600 < 38 ? 2 : 1;
  segs.forEach(([a, b], i) => {
    if (i > 0) { const x = gx(a) - gap / 2; svg += `<text class="g-day" x="${x}" y="11" text-anchor="middle">⋯</text>`; }
    svg += `<text class="g-day" x="${gx(a) + 2}" y="11">${dm(a)}</text>`;
    for (let t = Math.ceil(a / 3600 / hourStep) * 3600 * hourStep; t <= b; t += 3600 * hourStep) {
      const x = gx(t);
      if (x - gx(a) > 58 && gx(b) - x > 30) svg += `<text class="g-time" x="${x}" y="11" text-anchor="middle">${hm(t)}</text>`;
      svg += `<line class="g-grid" x1="${x}" x2="${x}" y1="${top - 4}" y2="${H}"/>`;
    }
  });
  drones.forEach((d, i) => {
    const y = top + i * rowH, off = S.hidden.has(d.id), col = S.colors[d.id];
    svg += `<g class="g-row"><rect class="g-bg" x="0" y="${y}" width="${W}" height="${rowH}" fill="transparent"/>
      <circle cx="8" cy="${y + rowH / 2}" r="4" fill="${col}" opacity="${off ? 0.3 : 1}"/><text class="g-lbl" x="18" y="${y + rowH / 2 + 4}" opacity="${off ? 0.4 : 1}">${esc(d.id)}</text>
      <text class="g-sub" x="${left - 8}" y="${y + rowH / 2 + 4}" text-anchor="end">${SHORT[d.model] || ""}</text>
      <text class="g-sub" x="${W - 4}" y="${y + rowH / 2 + 4}" text-anchor="end">${d.sorties.length ? fmt(d.flight_h, 1) + " ч" : "—"}</text>`;
    for (const s of r.sorties.filter((x) => x.drone_id === d.id)) {
      const sel = S.sel === s.id;
      const op = off ? 0.15 : (S.sel === null || sel ? 1 : 0.3);
      const x1 = gx(s.t_takeoff), x2 = gx(s.t_landing);
      svg += `<g class="g-bar" data-s="${s.id}" opacity="${op}"><rect x="${x1}" y="${y + 4}" width="${Math.max(2, x2 - x1)}" height="${rowH - 8}" rx="3" fill="${col}" opacity=".38"/>`;
      for (const p of s.phases || []) if (p.phase === "survey") {
        const a = gx(p.t_start), b = gx(p.t_end);
        if (b - a > 0.4) svg += `<rect x="${a}" y="${y + 4}" width="${b - a}" height="${rowH - 8}" fill="${col}"/>`;
      }
      if (sel) svg += `<rect x="${x1 - 1}" y="${y + 3}" width="${Math.max(2, x2 - x1) + 2}" height="${rowH - 6}" rx="4" fill="none" stroke="var(--ink)" stroke-width="1.5"/>`;
      svg += `</g>`;
    }
    svg += `</g>`;
  });
  svg += `<line class="g-cursor" x1="${gx(S.t)}" x2="${gx(S.t)}" y1="${top - 6}" y2="${H}"/></svg>`;
  box.innerHTML = svg;
  const tip = $("#g-tip");
  $$("#gantt .g-bar").forEach((el) => {
    el.addEventListener("click", () => selectSortie(+el.dataset.s, true));
    el.addEventListener("mousemove", (e) => {
      const s = r.sorties.find((x) => x.id === +el.dataset.s);
      const surv = (s.phases || []).filter((p) => p.phase === "survey").reduce((a, p) => a + p.t_end - p.t_start, 0);
      tip.hidden = false;
      tip.innerHTML = `<b>Вылет ${s.id} · ${esc(s.drone_id)}</b><br>${dur(s.takeoff_iso, s.landing_iso)} · ${fmt(s.duration_min, 0)} мин · АКБ ${fmt(s.battery_used_pct, 0)}%<br>съемка ${hmDur(surv)} (${fmt(surv / (s.t_landing - s.t_takeoff) * 100, 0)}%) · ${esc(s.site)}`;
      const rc = $("#dock").getBoundingClientRect();
      tip.style.left = `${Math.min(rc.width - 270, e.clientX - rc.left + 12)}px`;
      tip.style.top = `${e.clientY - rc.top - 70}px`;
    });
    el.addEventListener("mouseleave", () => { tip.hidden = true; });
  });
}
new ResizeObserver(() => drawGantt()).observe($("#gantt"));
new ResizeObserver(() => { $("#stage").style.setProperty("--dock-h", `${$("#dock").hidden || $("#dock").classList.contains("expanded") ? 0 : $("#dock").offsetHeight}px`); }).observe($("#dock"));
new ResizeObserver(() => { if (S.plan && !$("#dock").hidden) drawGantt(); }).observe($("#gantt"));
// клик по пустому месту расписания — перейти ко времени
$("#gantt").addEventListener("click", (e) => {
  if (e.target.closest(".g-bar") || !S.gx || !S.plan) return;
  const rc = $("#gantt svg").getBoundingClientRect(), x = e.clientX - rc.left;
  let lo = S.t0, hi = S.t1;
  for (let i = 0; i < 40; i++) { const m = (lo + hi) / 2; if (S.gx(m) < x) lo = m; else hi = m; }
  setTime(lo);
});

// ================================================================ история и сравнение
async function refreshHistory(preselect = []) {
  const selected = new Set(preselect.length ? preselect : $$("#history .hc").filter((c) => c.checked).map((c) => c.value));
  let list;
  try { list = await api("/api/plans"); } catch (e) { toast("Не удалось загрузить историю: " + e.message, "bad"); return; }
  if (!list.length) {
    $("#history-actions").innerHTML = "";
    $("#cmp").innerHTML = "";
    $("#history").innerHTML = `<div class="empty"><div class="empty-ico">${icon("history")}</div><div class="empty-t">История пуста</div><div class="hint">Здесь появятся рассчитанные планы — их можно открыть и сравнить.</div></div>`;
    return;
  }
  $("#history-actions").innerHTML = `<div class="cmp-actions"><button class="btn btn-sm" id="do-cmp" type="button">${ico("compare")}Сравнить отмеченные</button><span class="hint" id="cmp-selection" aria-live="polite"></span></div>`;
  $("#history").innerHTML = list.map((p) => { const s = p.summary || {}; const ok = p.status === "ok" && p.validation_passed;
      const partial = p.status === "infeasible" && p.validation_passed && s.sorties > 0;
      const statusText = ok ? "✓" : partial ? "частично" : p.status === "infeasible" && !s.sorties ? "невыполним" : "!";
      return `<div class="hist ${S.plan && S.plan.id === p.id ? "cur" : ""}"><input type="checkbox" class="hc" value="${p.id}" ${selected.has(p.id) ? "checked" : ""} aria-label="Отметить для сравнения">
      <div><div class="hist-h"><a href="#" data-load="${p.id}">${objectiveName(p.objective)}</a>
        <span class="badge ${ok ? "" : partial ? "warn" : "bad"}">${statusText}</span></div>
        <div class="hint">${esc((p.scenario_name || "").slice(0, 46))} · ${p.created ? new Date(p.created).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : ""}</div>
        <div class="hist-m"><span><b>${fmt(s.makespan_h)}</b> ч работ</span><span><b>${fmt(s.flight_hours)}</b> ч налета</span><span><b>${s.sorties ?? "—"}</b> вылетов</span>${s.coverage_allowed_pct != null ? `<span>покрытие <b>${fmt(s.coverage_allowed_pct)}%</b></span>` : ""}</div></div>
      <button class="eye" data-del="${p.id}" title="Удалить из истории">${icon("trash")}</button></div>`; }).join("");
  const updateSelection = (clearCompare = false) => {
    const count = $$("#history .hc").filter((c) => c.checked).length;
    $("#do-cmp").disabled = count < 2;
    $("#cmp-selection").textContent = count ? `Выбрано: ${count}${count < 2 ? " · нужно минимум 2" : ""}` : "Отметьте 2+ плана";
    if (clearCompare || count < 2) $("#cmp").innerHTML = "";
  };
  $$("#history .hc").forEach((c) => c.addEventListener("change", () => updateSelection(true)));
  updateSelection();
  $$("#history [data-load]").forEach((a) => a.addEventListener("click", async (e) => { e.preventDefault(); await openPlan(a.dataset.load); switchTab("overview"); }));
  $$("#history [data-del]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/plans/${b.dataset.del}`, { method: "DELETE" }); if (S.plan && S.plan.id === b.dataset.del) clearPlan(); refreshHistory(); } catch (e) { toast(e.message, "bad"); }
  }));
  $("#do-cmp").addEventListener("click", async () => {
    const ids = $$("#history .hc").filter((c) => c.checked).map((c) => c.value);
    if (ids.length < 2) return;
    const button = $("#do-cmp");
    button.disabled = true;
    $("#cmp").innerHTML = `<div class="hint">Загружаем планы для сравнения…</div>`;
    try { renderCompare(await Promise.all(ids.map((i) => api(`/api/plans/${encodeURIComponent(i)}`)))); }
    catch (e) { $("#cmp").innerHTML = `<div class="hint">Не удалось сравнить планы.</div>`; toast("Не удалось сравнить планы: " + e.message, "bad"); }
    finally { updateSelection(); }
  });
}
function renderCompare(plans) {
  plans = plans.filter((p) => p.summary && p.summary.makespan_h !== undefined);
  if (plans.length < 2) { $("#cmp").innerHTML = `<div class="hint">Для сравнения нужны два рассчитанных плана с итоговыми показателями.</div>`; return; }
  const rows = [
    ["Время работ, ч", (p) => p.summary.makespan_h, "min"], ["Суммарный налет, ч", (p) => p.summary.flight_hours, "min"],
    ["Вылетов", (p) => p.summary.sorties, "min"], ["Путь, км", (p) => p.summary.total_km, "min"],
    ["Покрытие, %", (p) => p.summary.coverage_allowed_pct, "max"], ["Бортов в работе", (p) => p.summary.drones_used, null],
  ];
  const head = plans.map((p) => `<th class="num">${esc(objectiveName(p.params?.objective || p.summary.objective))}</th>`).join("");
  const body = rows.map(([n, f, best]) => {
    const vals = plans.map((p) => f(p) == null ? NaN : Math.round(f(p) * 10) / 10);
    const finite = vals.filter(Number.isFinite);
    const b = finite.length && best === "min" ? Math.min(...finite) : finite.length && best === "max" ? Math.max(...finite) : null;
    return `<tr><td>${n}</td>${vals.map((v) => `<td class="num ${b !== null && v === b ? "best" : ""}">${fmt(v)}</td>`).join("")}</tr>`;
  }).join("");
  const shares = `<tr><td>Доли площади</td>${plans.map((p) => `<td class="num">${Object.entries(p.summary.shares || p.summary.allocation_shares || {}).filter(([, v]) => v > 0).map(([k, v]) => `${esc(SHORT[k] || k)} ${Math.round(v * 100)}%`).join("<br>") || "—"}</td>`).join("")}</tr>`;
  const checks = `<tr><td>Crash-тест</td>${plans.map((p) => `<td class="num">${p.validation?.passed ? '<span class="best">✓ пройден</span>' : "✖"}</td>`).join("")}</tr>`;
  $("#cmp").innerHTML = `<div class="section-h">Сравнение</div><div class="table-box" style="max-height:none"><table class="t"><tr><th></th>${head}</tr>${body}${shares}${checks}</table></div>
    <div class="hint" style="margin-top:6px">Зеленым — лучшее значение в строке.</div>`;
}
// план из истории открывается вместе со своим сценарием и параметрами
async function openPlan(id) {
  invalidatePlan();
  const revision = S.revision;
  const plan = await api(`/api/plans/${id}`);
  let sc = null;
  try { sc = await api(`/api/plans/${id}/scenario`); } catch (e) { /* план рассчитан до сохранения сценариев */ }
  if (revision !== S.revision) return;
  if (sc) {
    const demo = [...$("#scenario").options].find((o) => o.textContent === sc.name && !o.value.startsWith("plan:"));
    S.scenario = sc;
    if (demo) { $("#scenario").value = demo.value; S.scenarioId = demo.value; }
    else {
      const v = `plan:${id}`;
      if (![...$("#scenario").options].some((o) => o.value === v)) $("#scenario").insertAdjacentHTML("beforeend", `<option value="${v}">${esc(sc.name || "Сценарий")} · из истории</option>`);
      $("#scenario").value = v; S.scenarioId = v;
    }
    restoreParams(plan.params);
    drawScenario(S.scenario);
  } else if (S.scenario && S.scenario.name !== plan.scenario_name) {
    toast("Сценарий этого плана не сохранен — на карте текущий сценарий", "info", 6000);
  }
  loadPlan(plan);
  return plan;
}

// ================================================================ файлы и сценарии
$("#scenario").addEventListener("change", async (e) => {
  const v = e.target.value;
  if (!v || v.startsWith("file:")) return;
  if (v.startsWith("plan:")) { await openPlan(v.slice(5)); return; }
  S.extraSites = [];
  try {
    S.scenario = await api(`/api/scenarios/${v}`); S.scenarioId = v;
    setStartFromScenario(S.scenario); invalidatePlan(); drawScenario(S.scenario); store.set("scenario", v);
    showHint();
  } catch (err) { toast(err.message, "bad"); }
});
// обрезка препятствий до района работ (+~25 км): файл препятствий может покрывать целый регион
function cropObstacles(gj) {
  const work = gj.features.filter((f) => WORK_KINDS.has(f.properties.kind));
  if (!work.length) return gj;
  const b = L.geoJSON({ type: "FeatureCollection", features: work }).getBounds().pad(0);
  const box = L.latLngBounds([b.getSouth() - 0.25, b.getWest() - 0.35], [b.getNorth() + 0.25, b.getEast() + 0.35]);
  const feats = gj.features.filter((f) => f.properties.kind !== "obstacle" || box.intersects(L.geoJSON(f).getBounds()));
  return { ...gj, features: feats };
}
async function loadFiles(files) {
  if (!files.length) return;
  try {
    const fd = new FormData();
    [...files].forEach((f) => fd.append("files", f));
    const gj = await api("/api/scenario/parse", { method: "POST", body: fd });
    const kinds = new Set(gj.features.map((f) => f.properties.kind));
    const names = [...files].map((f) => f.name).join(", ");
    const nObs = gj.features.filter((f) => f.properties.kind === "obstacle").length;
    (gj.notes || []).forEach((n) => toast(n, "info", 9000));
    if (kinds.has("survey_area") || !S.scenario) {
      S.scenario = cropObstacles(gj); S.extraSites = []; S.scenarioId = `file:${names}`;
      setStartFromScenario(S.scenario);
      $("#scenario").insertAdjacentHTML("beforeend", `<option value="file:${esc(names)}" selected>${esc(names)}</option>`);
      toast(`Сценарий загружен: ${names}`, "ok");
      if (!kinds.has("site")) toast("В файлах нет площадок: поставьте их на карте (кнопка с булавкой справа) или сервис расставит их автоматически", "info", 10000);
    } else {
      // слой без области съемки — добавляем к текущему сценарию (например, файл высотных препятствий)
      const before = S.scenario.features.length;
      S.scenario = cropObstacles({ ...S.scenario, name: `${S.scenario.name || "Сценарий"} + ${names}`, features: S.scenario.features.concat(gj.features) });
      const added = S.scenario.features.length - before;
      toast(`Слой добавлен: ${names}. ${nObs ? `Препятствий в файле ${nObs}, в районе работ — ${added}` : `объектов: ${added}`}`, "ok", 7000);
    }
    invalidatePlan(); drawScenario(S.scenario);
  } catch (err) { toast(err.message, "bad", 9000); }
}
$("#file").addEventListener("change", async (e) => { await loadFiles(e.target.files); e.target.value = ""; });
// перетаскивание файлов на карту (можно несколько слоев сразу)
let dragDepth = 0;
const stage = $("#stage");
stage.addEventListener("dragenter", (e) => {
  if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
  e.preventDefault(); dragDepth++;
  if (!$(".drop-hint")) stage.insertAdjacentHTML("beforeend", `<div class="drop-hint">Отпустите файлы GeoJSON / KML — область, зоны, препятствия</div>`);
});
stage.addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $(".drop-hint")?.remove(); });
stage.addEventListener("dragover", (e) => e.preventDefault());
stage.addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; $(".drop-hint")?.remove(); if (e.dataTransfer.files.length) loadFiles(e.dataTransfer.files); });

// ================================================================ подсказки и клавиатура
function showHint() {
  if (S.scenario) {
    const n = S.scenario.features.filter((f) => f.properties.kind === "survey_area").length, a = areaKm2(S.scenario);
    $("#hint-eta").textContent = a > 150 || n > 100 ? "несколько минут (большое задание: до 10 мин в режиме «быстро»)" : "10–30 секунд";
  }
  $("#map-hint").hidden = !!S.plan || store.get("hint.x", false) || busy;
}
$("#hint-x").addEventListener("click", () => { store.set("hint.x", true); $("#map-hint").hidden = true; });
$("#hint-run").addEventListener("click", () => onRun([S.objective]));
const helpOpen = (v) => { $("#help-pop").hidden = !v; };
$("#help").addEventListener("click", () => helpOpen(true));
$("#help-x").addEventListener("click", () => helpOpen(false));
$("#help-pop").addEventListener("click", (e) => { if (e.target.id === "help-pop") helpOpen(false); });
document.addEventListener("keydown", (e) => {
  const el = document.activeElement;
  const typing = el.closest("input, select, textarea, button, a, [role=radio], [role=checkbox], [role=slider], [contenteditable=true]");
  if (el.closest("#assistant") && e.key !== "Escape") return;
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); onRun([S.objective]); return; }
  if (e.key === "Escape") {
    if (!$("#assistant").hidden) { window.product?.closeAssistant(); return; }
    if (!$("#help-pop").hidden) helpOpen(false);
    else if (busy) cancelRun();
    else if (S.addSite) setAddSite(false);
    else { clearSelection(); showLayers(false); }
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === " " && el.id !== "compass") { e.preventDefault(); togglePlay(); }
  else if (e.key === "ArrowRight" && el.id !== "compass") { e.preventDefault(); stepSortie(1); }
  else if (e.key === "ArrowLeft" && el.id !== "compass") { e.preventDefault(); stepSortie(-1); }
  else if (e.key === "?") helpOpen($("#help-pop").hidden);
  else if (e.key === "1") switchTab("task");
  else if (e.key === "2") switchTab("overview");
  else if (e.key === "3") switchTab("result");
  else if (e.key === "4") switchTab("history");
  else if (e.key === "f" || e.key === "F" || e.key === "а" || e.key === "А") { S.userMoved = false; fitScenario(); }
});
$("#run").addEventListener("click", () => onRun([S.objective]));
$("#run2").addEventListener("click", () => onRun([S.objective]));
$("#compare").addEventListener("click", () => onRun(["time", "flight", "balanced"]));

// ================================================================ старт
(async function init() {
  paintIcons();
  updateOnboarding();
  applyTheme(store.get("theme", window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  setWindDir(250);
  try {
    const session = await api("/api/session");
    storageScope = session.id;
    setZoneLabelsVisible(!store.get("off.zoneLabels", false), false);
    setPanelVisible(!store.get("panel.collapsed", false), false);
    S.colorMode = store.get("colormode", "drone"); setChip("#colormode", S.colorMode);
    if (store.get("dock.collapsed", false)) $("#dock").classList.add("collapsed");
    S.catalog = await api("/api/catalog");
    legend();
    const savedF = store.get("fleet", null), savedB = store.get("bats", null);
    for (const [k, d] of Object.entries(S.catalog.drones)) {
      S.fleet[k] = savedF && k in savedF ? savedF[k] : (DEFAULT_FLEET[k] ?? 0);
      S.bats[k] = savedB && k in savedB ? savedB[k] : d.batteries;
    }
    renderFleet(); hints();
    const sc = await api("/api/scenarios");
    $("#scenario").innerHTML = sc.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("");
    // последний план — сразу на экран вместе со своим сценарием; иначе — сохраненный/первый демо-сценарий
    const hist = await api("/api/plans");
    if (hist.length >= 2 && missionRunAttempts < 2) {
      missionRunAttempts = 2;
      store.set("onboarding.attempts", missionRunAttempts);
      updateOnboarding();
    }
    const last = hist.find((h) => h.summary && h.summary.makespan_h !== undefined && h.has_scenario);
    if (last) { await openPlan(last.id); }
    else {
      const saved = store.get("scenario", null);
      if (saved && sc.some((x) => x.id === saved)) $("#scenario").value = saved;
      if (sc.length) $("#scenario").dispatchEvent(new Event("change"));
    }
    summarize();
  } catch (e) { toast("Нет связи с сервером: " + e.message, "bad", 10000); }
})();
