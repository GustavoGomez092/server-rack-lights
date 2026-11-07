// Rack status screen: renders /api/screen/stream pushes. No input; it only has to look right.
"use strict";

const CIRC = 2 * Math.PI * 40;
const STALE_MS = 10000;
const $ = (sel) => document.querySelector(sel);
let lastData = 0;

const gb = (bytes) => {
  const v = bytes / 1024 ** 3;
  return v >= 100 ? v.toFixed(0) : v.toFixed(1);
};
const level = (v, warn, bad) => (v >= bad ? "bad" : v >= warn ? "warn" : "");

// Server-local wall time straight from the ISO string, so the browser's own timezone never matters.
function clock(iso) {
  const m = /T(\d\d):(\d\d)/.exec(iso || "");
  if (!m) return "";
  const h = Number(m[1]);
  return `${h % 12 || 12}:${m[2]} ${h < 12 ? "AM" : "PM"}`;
}

function uptime(s) {
  if (s == null) return "";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
  if (d >= 1) return `up ${d} day${d === 1 ? "" : "s"}`;
  return h >= 1 ? `up ${h} h` : `up ${Math.max(1, Math.floor(s / 60))} min`;
}

function gauge(id, pct, text, cls, detail) {
  const el = $(id);
  const p = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  el.querySelector(".arc").style.strokeDasharray = `${(p / 100) * CIRC} ${CIRC}`;
  el.querySelector(".num").textContent = text;
  el.className = `gauge ${cls || ""}`;
  const d = el.querySelector(".detail");
  if (d) d.textContent = detail || "";
}

function renderStats(s) {
  if (!s) return;
  gauge("#g-cpu", s.cpu_pct, s.cpu_pct == null ? "–" : Math.round(s.cpu_pct), level(s.cpu_pct, 80, 95));
  const m = s.memory;
  const ramPct = m ? (100 * m.used_bytes) / m.total_bytes : null;
  gauge("#g-ram", ramPct, m ? Math.round(ramPct) : "–", level(ramPct, 85, 95),
        m ? `${gb(m.used_bytes)} / ${gb(m.total_bytes)} GB` : "");
  const t = s.temps || {};
  // The ring spans 30–100 °C, so a resting CPU sits low and a hot one fills it.
  gauge("#g-temp", t.cpu_c == null ? null : ((t.cpu_c - 30) / 70) * 100, t.cpu_c == null ? "–" : Math.round(t.cpu_c),
        level(t.cpu_c, 75, 85), t.drive_c == null ? "" : `· drive ${Math.round(t.drive_c)}°`);

  $("#disks").replaceChildren(...(s.disks || []).filter(Boolean).map((d) => {
    const row = document.createElement("div");
    row.className = `disk ${d.mounted ? level(d.pct, 85, 95) : "absent"}`;
    const used = d.mounted ? `${gb(d.used_bytes)} / ${gb(d.total_bytes)} GB` : "not connected";
    row.innerHTML = `<div class="row"><span></span><span></span></div><div class="track"><div class="fill"></div></div>`;
    row.querySelector(".row span:first-child").textContent = d.label;
    row.querySelector(".row span:last-child").textContent = used;
    row.querySelector(".fill").style.width = `${d.mounted ? d.pct : 0}%`;
    return row;
  }));

  const apps = s.apps || [];
  $("#apps").replaceChildren(...(apps.length ? apps.map((a) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="name"></span><span class="cpu"></span><span class="mem"></span>`;
    li.querySelector(".name").textContent = a.name;
    li.querySelector(".cpu").textContent = `${a.cpu_pct < 10 ? a.cpu_pct.toFixed(1) : Math.round(a.cpu_pct)}%`;
    li.querySelector(".mem").textContent = `${gb(a.mem_bytes)} GB`;
    return li;
  }) : [Object.assign(document.createElement("li"), { className: "empty", textContent: "No app data yet" })]));
}

function renderBanner(items) {
  const el = $("#banner");
  el.hidden = !items.length;
  el.replaceChildren(...items.map((b) => {
    const row = document.createElement("div");
    row.className = `alert ${b.priority === "critical" || b.priority === "high" ? "bad" : ""}`;
    row.innerHTML = `<span></span><time></time>`;
    row.querySelector("span").textContent = b.text;
    row.querySelector("time").textContent = clock(b.since);
    return row;
  }));
}

function render(data) {
  lastData = Date.now();
  document.body.classList.remove("stale");
  document.documentElement.dataset.theme = data.screen.theme;
  $("#host").textContent = data.host.name || "Command Center";
  const up = data.stats ? uptime(data.stats.uptime_s) : "";
  $("#meta").textContent = [up, clock(data.now)].filter(Boolean).join(" · ");
  renderBanner(data.banner || []);
  renderStats(data.stats);
  requestAnimationFrame(() => document.body.classList.add("ready"));
}

// EventSource reconnects by itself after a led-notify restart; meanwhile the last numbers stay up, dimmed.
const source = new EventSource("../api/screen/stream");
source.addEventListener("screen", (e) => render(JSON.parse(e.data)));
setInterval(() => {
  if (Date.now() - lastData > STALE_MS) document.body.classList.add("stale");
}, 2000);
