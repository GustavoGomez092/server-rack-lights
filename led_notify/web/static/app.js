"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hexToRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const rgbToHex = (rgb) => "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("");
const getPath = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

function setPath(obj, path, value) {
  const keys = path.split(".");
  let o = obj;
  keys.slice(0, -1).forEach((k) => { o = o[k] ??= {}; });
  o[keys.at(-1)] = value;
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d
      : Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ")
      : `HTTP ${res.status}`);
  }
  return data;
}

let toastTimer;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = bad ? "bad" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

async function act(fn, okMsg) {
  try {
    await fn();
    if (okMsg) toast(okMsg);
  } catch (e) {
    toast(e.message, true);
  }
}

const state = { catalog: { named: [], effects: [] }, rules: {}, settings: {}, devices: [], log: [], sounds: [] };

/* ---------- header ---------- */
function renderState(s) {
  const strip = s.strip || {};
  const el = $("#strip");
  el.textContent = strip.connected ? (strip.fake ? "Fake strip" : "Strip connected")
    : `Strip offline${strip.attempt ? ` · retry ${strip.attempt}` : ""}`;
  el.title = strip.last_error || "";
  el.className = "pill " + (strip.connected ? "ok" : "bad");
  const c = s.current;
  $("#now").textContent = c ? `${c.pattern} ← ${c.reason}` : "—";
  $("#active").innerHTML = (s.active || []).map((a) => `<span class="pill p-${esc(a.priority)}">${esc(a.name)}</span>`).join(" ")
    || '<span class="muted">none</span>';
  $("#monitors").innerHTML = Object.entries(s.monitors || {})
    .map(([n, h]) => `<span class="mon ${esc(h.status)}" title="${esc(h.status)} ${esc(h.detail)}">${esc(n)}</span>`).join(" ")
    || '<span class="muted">none</span>';
  $("#manual-status").textContent = s.manual ? `Manual: ${s.manual.desc} · ${Math.ceil(s.manual.expires_in_s / 60)} min left` : "Automatic";
  $("#quiet").hidden = !s.quiet;
  const audioErr = (s.audio && s.audio.last_error) || "";
  $("#audio").hidden = !audioErr;   // only shown when the last chime failed
  $("#audio").title = audioErr;     // a DOM property, so no HTML escaping is needed
  $("#warnings").innerHTML = (s.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("");
}
const loadState = () => api("GET", "/api/state").then(renderState).catch(() => {});

/* ---------- tabs ---------- */
function showTab(name) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$("main > section").forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  if (name === "devices") loadDevices();
  if (name === "rules") loadRulesAndSettings();
  if (name === "log") loadLog();
}
$$("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ---------- test tab ---------- */
function renderEventButtons() {
  $("#event-buttons").innerHTML = Object.entries(state.rules).map(([name, r]) =>
    `<button class="event p-${esc(r.priority)}" data-name="${esc(name)}"><b>${esc(name)}</b><small>${esc(r.pattern)} · ${esc(r.priority)}</small></button>`).join("");
}
$("#event-buttons").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-name]");
  if (!b) return;
  const force = $("#force").checked;
  act(async () => {
    const r = await api("POST", `/api/test/${encodeURIComponent(b.dataset.name)}?force=${force}`);
    toast(`${b.dataset.name}: ${r.outcome}`);
  });
});

function renderGallery() {
  const q = $("#gallery-search").value.trim().toLowerCase();
  const items = [
    ...state.catalog.named.map((n) => ({ label: n, sub: "pattern", body: { pattern: n } })),
    ...state.catalog.effects.map((e) => ({ label: e.name, sub: `effect ${e.fx}`, body: { effect: e.fx } })),
  ].filter((i) => !q || i.label.toLowerCase().includes(q) || i.sub.includes(q));
  $("#gallery").innerHTML = items.map((i, idx) =>
    `<button class="swatch" data-idx="${idx}"><b>${esc(i.label)}</b><small>${esc(i.sub)}</small></button>`).join("");
  $("#gallery").onclick = (e) => {
    const b = e.target.closest("button[data-idx]");
    if (!b) return;
    const item = items[Number(b.dataset.idx)];
    act(() => api("POST", "/api/preview", { ...item.body, duration: 8 }), `Previewing ${item.label}`);
  };
}
$("#gallery-search").addEventListener("input", renderGallery);

/* ---------- manual tab ---------- */
$("#m-bright").addEventListener("input", (e) => { $("#m-bright-out").textContent = e.target.value; });
$("#m-speed").addEventListener("input", (e) => { $("#m-speed-out").textContent = e.target.value; });
$("#m-set-color").addEventListener("click", () => act(() =>
  api("POST", "/api/strip", { color: hexToRgb($("#m-color").value), brightness: Number($("#m-bright").value) }).then(renderState), "Color set"));
$("#m-set-effect").addEventListener("click", () => act(() =>
  api("POST", "/api/strip", { effect: Number($("#m-effect").value), speed: Number($("#m-speed").value), brightness: Number($("#m-bright").value) }).then(renderState), "Effect set"));
$("#m-off").addEventListener("click", () => act(() => api("POST", "/api/strip", { power: false }).then(renderState), "Strip off (manual)"));
$("#m-resume").addEventListener("click", () => act(() => api("DELETE", "/api/strip/manual").then(renderState), "Back to automatic"));
function renderEffectSelect() {
  $("#m-effect").innerHTML = state.catalog.effects.map((e) => `<option value="${e.fx}">${e.fx} · ${esc(e.name)}</option>`).join("");
}

/* ---------- devices tab ---------- */
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
async function loadDevices() {
  await act(async () => { state.devices = await api("GET", "/api/devices"); renderDevices(); });
}
function renderDevices() {
  const f = $("#dev-filter").value;
  const rows = state.devices.filter((d) => f === "all" || (f === "unknown" && !d.known) || (f === "online" && d.online));
  $("#dev-count").textContent = `${rows.length} of ${state.devices.length}`;
  $("#dev-table tbody").innerHTML = rows.map((d) => `
    <tr data-mac="${esc(d.mac)}">
      <td><input class="dev-name" value="${esc(d.name)}" placeholder="unnamed"></td>
      <td><code>${esc(d.mac)}</code>${d.private_mac ? ' <span class="pill tiny">private</span>' : ""}<br><small class="muted">${esc(d.vendor || "unknown vendor")}</small></td>
      <td>${esc(d.ip)}</td>
      <td><span class="mon ${d.online ? "ok" : "off"}">${d.online ? "online" : ago(d.last_seen_ts)}</span><br><small class="muted">first ${esc((d.first_seen || "").slice(0, 10))}</small></td>
      <td><input type="checkbox" class="dev-known" ${d.known ? "checked" : ""}></td>
      <td><input type="checkbox" class="dev-announce" ${d.announce_arrival ? "checked" : ""}></td>
      <td><button class="link dev-forget">Forget</button></td>
    </tr>`).join("");
}
$("#dev-filter").addEventListener("change", renderDevices);
$("#dev-table").addEventListener("change", (e) => {
  const tr = e.target.closest("tr[data-mac]");
  if (!tr) return;
  let patch;
  if (e.target.classList.contains("dev-name")) patch = { name: e.target.value.trim() };
  if (e.target.classList.contains("dev-known")) patch = { known: e.target.checked };
  if (e.target.classList.contains("dev-announce")) patch = { announce_arrival: e.target.checked };
  if (patch) {
    act(async () => {
      try {
        await api("PATCH", `/api/devices/${encodeURIComponent(tr.dataset.mac)}`, patch);
      } catch (err) {
        await loadDevices();
        throw err;
      }
    }, "Saved");
  }
});
$("#dev-table").addEventListener("click", (e) => {
  if (!e.target.classList.contains("dev-forget")) return;
  const mac = e.target.closest("tr").dataset.mac;
  if (!confirm(`Forget ${mac}? It will alert as a new device the next time it is seen.`)) return;
  act(async () => { await api("DELETE", `/api/devices/${encodeURIComponent(mac)}`); await loadDevices(); }, "Forgotten");
});

/* ---------- rules + settings tab ---------- */
const FIELDS = [
  ["Quiet hours", [["quiet_hours.enabled", "checkbox", "Enabled"], ["quiet_hours.start", "time", "Start"], ["quiet_hours.end", "time", "End"],
    ["quiet_hours.brightness_pct", "number", "Brightness %"], ["quiet_hours.idle_off", "checkbox", "Idle light off at night"]]],
  ["Idle & brightness", [["idle.mode", "select", "Idle mode", ["glow", "breath"]], ["idle.color", "color", "Glow color"],
    ["idle.breath_color", "select", "Breath color", ["red", "green", "blue", "yellow", "cyan", "purple", "white"]],
    ["idle.breath_speed", "number", "Breath speed (1-10)"], ["idle.brightness", "number", "Idle brightness (0-255)"],
    ["max_brightness", "number", "Max brightness (0-255)"], ["manual_timeout_min", "number", "Manual timeout (min)"]]],
  ["Audio", [["audio.enabled", "checkbox", "Chimes enabled"], ["audio.volume_pct", "number", "Volume % (day)"],
    ["audio.night_volume_pct", "number", "Night volume % (critical only)"]]],
  ["Internet", [["internet.slow_rtt_ms", "number", "Slow if median ping above (ms)"], ["internet.slow_loss_pct", "number", "Slow if packet loss above (%)"]]],
  ["Disk & CPU", [["disk.full_pct", "number", "Disk full at (%)"], ["disk.clear_pct", "number", "Clear below (%)"],
    ["thermal.hot_c", "number", "CPU hot at (°C)"], ["thermal.clear_c", "number", "Clear below (°C)"]]],
  ["AdGuard", [["adguard.url", "text", "URL"], ["adguard.username", "text", "Username"], ["adguard.password", "secret", "Password"]]],
  ["Webhook", [["webhook_token", "secret", "Bearer token (optional)"]]],
];
const opts = (values, selected) => values.map((v) => `<option ${v === selected ? "selected" : ""}>${esc(v)}</option>`).join("");

async function loadRulesAndSettings() {
  await act(async () => {
    [state.rules, state.settings, state.sounds] = await Promise.all([
      api("GET", "/api/rules"), api("GET", "/api/settings"), api("GET", "/api/sounds")]);
    renderRules();
    renderSettings();
    renderSounds();
    renderEventButtons();
  });
}
const soundOptions = (selected) => `<option value="" ${selected ? "" : "selected"}>(silent)</option>` +
  (selected && !state.sounds.some((s) => s.ref === selected)
    ? `<option value="${esc(selected)}" selected>${esc(selected)} (missing)</option>` : "") +
  state.sounds.map((s) => `<option value="${esc(s.ref)}" ${s.ref === selected ? "selected" : ""}>${esc(s.ref)}</option>`).join("");
function renderRules() {
  $("#rules-table tbody").innerHTML = Object.entries(state.rules).map(([name, r]) => `
    <tr data-name="${esc(name)}">
      <td><b>${esc(name)}</b><br><small class="muted">${esc(r.kind)}</small></td>
      <td><input type="checkbox" data-k="enabled" ${r.enabled ? "checked" : ""}></td>
      <td><select data-k="priority">${opts(["low", "normal", "high", "critical"], r.priority)}</select></td>
      <td><select data-k="pattern">${opts(state.catalog.named, r.pattern)}</select></td>
      <td class="nowrap"><select data-k="sound">${soundOptions(r.sound)}</select>
        <button type="button" class="link play-sound" title="Preview sound">▶</button></td>
      <td><input type="number" data-k="duration_s" min="0.5" max="60" step="0.5" value="${r.duration_s ?? ""}" placeholder="auto"></td>
      <td><select data-k="quiet_behavior">${opts(["dim", "suppress"], r.quiet_behavior)}</select></td>
    </tr>`).join("");
}
$("#save-rules").addEventListener("click", () => {
  const rules = structuredClone(state.rules);
  $$("#rules-table tbody tr").forEach((tr) => {
    const r = rules[tr.dataset.name];
    $$("[data-k]", tr).forEach((el) => {
      const k = el.dataset.k;
      r[k] = el.type === "checkbox" ? el.checked
        : k === "duration_s" ? (el.value === "" ? null : Number(el.value))
        : k === "sound" ? (el.value || null)
        : el.value;
    });
  });
  act(async () => { state.rules = await api("PUT", "/api/rules", rules); renderEventButtons(); }, "Rules saved");
});
function renderSettings() {
  $("#settings-form").innerHTML = FIELDS.map(([title, fields]) => `<fieldset><legend>${esc(title)}</legend>${fields.map(([path, type, label, choices]) => {
    const v = getPath(state.settings, path);
    if (type === "checkbox") return `<label class="inline"><input type="checkbox" data-path="${path}" ${v ? "checked" : ""}> ${esc(label)}</label>`;
    if (type === "color") return `<label>${esc(label)} <input type="color" data-path="${path}" data-type="color" value="${rgbToHex(v)}"></label>`;
    if (type === "select") return `<label>${esc(label)} <select data-path="${path}" data-type="select">${opts(choices, v)}</select></label>`;
    if (type === "secret") return `<label>${esc(label)} <input type="password" data-path="${path}" data-type="secret" autocomplete="new-password" placeholder="${v && v.set ? "set — leave blank to keep" : "not set"}"></label>`;
    return `<label>${esc(label)} <input type="${type}" data-path="${path}" data-type="${type}" value="${esc(v)}"></label>`;
  }).join("")}</fieldset>`).join("");
}
$("#save-settings").addEventListener("click", () => {
  const patch = {};
  $$("#settings-form [data-path]").forEach((el) => {
    const t = el.dataset.type;
    if (el.type === "checkbox") setPath(patch, el.dataset.path, el.checked);
    else if (t === "secret") { if (el.value) setPath(patch, el.dataset.path, el.value); }
    else if (t === "color") setPath(patch, el.dataset.path, hexToRgb(el.value));
    else if (t === "number") setPath(patch, el.dataset.path, Number(el.value));
    else setPath(patch, el.dataset.path, el.value);
  });
  act(async () => { state.settings = await api("PUT", "/api/settings", patch); renderSettings(); }, "Settings saved");
});

async function playSound(ref) {
  if (!ref) { toast("Silent: nothing to play"); return; }
  await act(() => api("POST", "/api/sounds/play", { sound: ref }), `Playing ${ref}`);
}
$("#rules-table").addEventListener("click", (e) => {
  const b = e.target.closest(".play-sound");
  if (b) playSound($("select[data-k=sound]", b.closest("tr")).value);
});
function renderSounds() {
  $("#sounds-table tbody").innerHTML = state.sounds.map((s) => `
    <tr data-ref="${esc(s.ref)}" data-name="${esc(s.name)}">
      <td><code>${esc(s.ref)}</code>${s.builtin ? ' <span class="pill tiny">built-in</span>' : ""}</td>
      <td>${s.duration_s == null ? "?" : esc(s.duration_s) + " s"}</td>
      <td class="nowrap"><button type="button" class="link play-sound-row">▶ Play</button>
        ${s.builtin ? "" : '<button type="button" class="link sound-delete">Delete</button>'}</td>
    </tr>`).join("");
}
$("#sounds-table").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-ref]");
  if (!tr) return;
  if (e.target.classList.contains("play-sound-row")) playSound(tr.dataset.ref);
  if (e.target.classList.contains("sound-delete")) {
    if (!confirm(`Delete ${tr.dataset.ref}? Rules using it switch to "default".`)) return;
    act(async () => {
      const r = await api("DELETE", `/api/sounds/${encodeURIComponent(tr.dataset.name)}`);
      await loadRulesAndSettings();
      toast(r.rules_reset.length ? `Deleted; reset ${r.rules_reset.join(", ")} to default` : "Deleted");
    });
  }
});
$("#sound-upload").addEventListener("click", () => {
  const file = $("#sound-file").files[0];
  if (!file) { toast("Choose a WAV or MP3 file first", true); return; }
  const type = file.type || (file.name.toLowerCase().endsWith(".mp3") ? "audio/mpeg" : "audio/wav");
  act(async () => {
    const res = await fetch(`/api/sounds/${encodeURIComponent(file.name)}`, { method: "PUT", headers: { "Content-Type": type }, body: file });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
    $("#sound-file").value = "";
    await loadRulesAndSettings();
    toast(`Uploaded ${data.ref}`);
  });
});

/* ---------- log tab ---------- */
async function loadLog() {
  await act(async () => { state.log = (await api("GET", "/api/log")).reverse(); renderLog(); });
}
function renderLog() {
  $("#log-table tbody").innerHTML = state.log.map((e) => `<tr>
    <td>${esc(e.time.slice(11, 19))}</td>
    <td>${esc(e.name)} <small class="muted">${esc(e.kind)}</small></td>
    <td>${esc(e.source)}</td>
    <td><span class="outcome ${esc(e.outcome.split(":")[0])}">${esc(e.outcome)}</span></td></tr>`).join("");
}
function addLog(entry) {
  state.log.unshift(entry);
  state.log.length = Math.min(state.log.length, 200);
  if (!$("#tab-log").hidden) renderLog();
}

/* ---------- live updates + boot ---------- */
function connectStream() {
  const es = new EventSource("/api/stream");
  es.addEventListener("state", (e) => renderState(JSON.parse(e.data)));
  es.addEventListener("log", (e) => addLog(JSON.parse(e.data)));
  es.addEventListener("devices", () => {
    // Don't re-render under the user's cursor: it would wipe a name being typed.
    const editing = document.activeElement && document.activeElement.matches("#dev-table input");
    if (!$("#tab-devices").hidden && !editing) loadDevices();
  });
}

(async function init() {
  await act(async () => {
    [state.catalog, state.rules] = await Promise.all([api("GET", "/api/patterns"), api("GET", "/api/rules")]);
    renderEventButtons();
    renderGallery();
    renderEffectSelect();
  });
  loadState();
  connectStream();
  setInterval(loadState, 10000);
})();
