# Native Rack Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Chromium kiosk in `led-notify-display` with a native Rust/GPUI program that draws the same rack screen for ≤ 150 MiB.

**Architecture:** A Cargo workspace in `display/screen/`: `core` (pure Rust, no GPUI: payload types, formatting, view model, theme/dimming, tween math, SSE parser — tested with `cargo test` on the Mac) and `app` (the GPUI binary `rack-screen`: reads `/api/screen/stream`, renders the view model). cage stays as the Wayland compositor and runs `screen-loop.sh`, which restarts `rack-screen` without ever letting cage exit. Built on the server by `./deploy.sh` with a multi-stage Dockerfile. A go/no-go spike comes first; if GPUI costs > 150 MiB the plan stops (see Task 1).

**Tech Stack:** Rust (edition 2021), `gpui` 0.2.2 (Vulkan via blade on Linux, Mesa RADV), `serde`/`serde_json`, `ureq` (blocking HTTP, default features off), cage + seatd (Debian trixie), bash, pytest for `run.sh`.

**Spec:** `docs/superpowers/specs/2026-11-09-native-rack-screen-design.md`

## Global Constraints

- **Start only after the RAM upgrade:** `ssh zima free -g` total ≥ 28. Before that, do nothing on the server except reading.
- Memory budget: container `docker stats` + Δ GPU GTT + Δ VRAM ≤ **150 MiB** after ≥ 1 h live; no growth over a day.
- **Never disable the monitor output** (`wlr-randr --off`, DPMS, killing cage while the program restarts). If the monitor sleeps: stop, ask the owner to unplug/replug its USB power, never loop.
- Look = reference PNGs `docs/superpowers/specs/assets/2026-11-09-rack-screen-ref-{dark,light,banner}.png` at 800×480, rendered from `display/screen/fixtures/{dark,light,banner}.json`. Values from git `f2e4583:led_notify/web/static/screen/screen.{css,js}` (1vw = 8 px, 1vh = 4.8 px at 800×480).
- No continuous redraw: idle between pushes; animation frames only while a tween runs.
- SSH only via `-o ControlMaster=auto -o ControlPath=/tmp/led-notify-ssh-%r@%h:%p -o ControlPersist=600` (sshd bans aborted connections). Never print secrets.
- Server builds use `CARGO_BUILD_JOBS=3`.
- Merges and pushes only with the owner's explicit OK. Commits end with `Claude-Session: https://claude.ai/code/session_01LTKLaTGfqiRkKL437D9Bvc`.
- Python suite stays green: `.venv/bin/pytest -q` (374 passing before this work).

## Review Focus

1. **led-notify down at program start or mid-run** (deploy order, restarts): the program must show the empty layout / dimmed last numbers with ` · reconnecting` and reconnect forever — never exit in a loop fast enough to churn, never show an error. Covered by Task 4 (parser + staleness) and Task 6 (reconnect loop, manual check by stopping `led-notify`).
2. **A half-open connection** (led-notify killed hard, no FIN): the stream must time out (read timeout 15 s) and reconnect, or the screen freezes "fresh". Task 6 sets the timeout; check by `docker kill led-notify`.
3. **Malformed or partial payload** (a `null` stats, a missing `temps.cpu_c`, `apps: []`, unmounted disk): no panic; `–`/empty ring/`not connected`/`No app data yet`. Covered by Task 2 tests.
4. **Theme or brightness change while values animate**, and `power: off` → `on`: no flash of the wrong theme on the first frame, black is pure #000. Task 3 tests + Task 5 fixture checks.
5. **Long names / big numbers** (`"Calibre-Web Automated"`, 1000+ GB, 100 % CPU): ellipsis, no overlap at 800×480. Task 5 checks with an edited fixture.

---

### Task 1: Go/no-go spike and the memory meter

**Files:**
- Create: `display/measure.sh` (kept)
- Create: `display/spike/Cargo.toml`, `display/spike/src/main.rs`, `display/spike/Dockerfile` (throwaway, deleted at the end of the task)

**Interfaces:**
- Produces: `display/measure.sh snapshot` → one line `container_mib=N gtt_mib=N vram_mib=N avail_mib=N` (container = `docker stats --no-stream` mem of `led-notify-display`, 0 when stopped; gtt/vram from `/sys/class/drm/card0/device/mem_info_{gtt,vram}_used`; avail from `/proc/meminfo` MemAvailable). `display/measure.sh cost` → stops `led-notify-display`, waits 20 s, snapshot A; starts it, waits `${SETTLE_S:-600}` s, snapshot B; prints `cost_mib = container_B + (gtt_B - gtt_A) + (vram_B - vram_A)` and `avail_drop_mib = avail_A - avail_B`. Runs on the server (`ssh zima 'bash -s' < display/measure.sh cost`).

- [ ] **Step 1: Write `display/measure.sh`**, then run `snapshot` against today's Chromium screen.
  Run: `ssh … zima 'bash -s' < display/measure.sh snapshot`
  Expected: `container_mib≈228 gtt_mib≈12 vram_mib≈20 avail_mib=…`
- [ ] **Step 2: Baseline cost of the Chromium screen** (stopping the container hands the output to the console for a moment; known safe).
  Run: `ssh … zima 'SETTLE_S=600 bash -s' < display/measure.sh cost`
  Expected: `cost_mib` ≈ 230–300. Record it in the ledger as the baseline.
- [ ] **Step 3: Keep the Chromium image for rollback**: `ssh … zima sudo docker tag led-notify-display:local led-notify-display:chromium`.
- [ ] **Step 4: Spike program** — `gpui = "0.2.2"`; opens one window (full screen, no decorations, background `#0b0f14`), draws `GomezPalacios` in Inter 19 px and one ring (r = 40 % of 120 px, stroke 9/100 of the ring size, color `#4cc2ff`) whose value changes every 2 s (driven by a GPUI timer, re-render only then). Spike Dockerfile: multi-stage (builder `rust:1-slim-trixie` + the `-dev` packages GPUI needs — add until it links and list them in the ledger; runtime = today's `display/Dockerfile` minus `chromium`, plus `mesa-vulkan-drivers libvulkan1 libwayland-client0 libxkbcommon0 libfontconfig1 grim`, `WLR_RENDERER=gles2`), entry runs `cage -- /usr/local/bin/spike` under seatd like `run.sh`.
- [ ] **Step 5: Build on the server and swap it in**:
  Run: `rsync display/spike → zima:/DATA/AppData/led-notify/spike`, then `sudo docker build -t led-notify-display:local …/spike && sudo docker compose -f /DATA/AppData/led-notify/src/docker-compose.yml up -d display`
  Expected: the owner sees the ring on the monitor (ask them, one line); `docker logs led-notify-display` shows no Vulkan/EGL errors.
- [ ] **Step 6: Measure**: `SETTLE_S=600 … measure.sh cost`.
  Expected: `cost_mib ≤ 150` → **GO**. Also note idle CPU from `docker stats` (must be ≤ ~2 %).
  **If `cost_mib > 150` → NO-GO: restore Chromium (Step 7), write the numbers in the ledger, stop and tell the owner; the fallback (plain Wayland client + software rasterizer) gets its own plan. Do not continue to Task 2's app work.**
- [ ] **Step 7: Restore Chromium**: `sudo docker tag led-notify-display:chromium led-notify-display:local && sudo docker compose … up -d display`. Expected: status page back on the monitor.
- [ ] **Step 8: Commit** `display/measure.sh` only (`git rm -r display/spike` if it was added; it stays out of git).
  `git commit -m "feat(display): measure.sh, the rack screen's memory cost"`

### Task 2: core — payload, formatting, view model

**Files:**
- Create: `display/screen/Cargo.toml` (workspace: `core`, `app`), `display/screen/core/Cargo.toml`, `display/screen/core/src/lib.rs`, `display/screen/core/src/payload.rs`, `display/screen/core/src/format.rs`, `display/screen/core/src/view.rs`
- Test: `display/screen/core/tests/view.rs` (uses `display/screen/fixtures/*.json`)

**Interfaces:**
- Produces (crate `rack_screen_core`):
  - `payload::ScreenData` (serde, mirrors the JSON: `screen{power,theme,brightness}`, `stats: Option<Stats{cpu_pct: Option<f64>, memory: Option<{used_bytes,total_bytes}>, temps: Option<{cpu_c: Option<f64>, drive_c: Option<f64>}>, disks: Vec<Disk{label,mounted,used_bytes: Option<u64>,total_bytes: Option<u64>,pct: Option<f64>}>, apps: Vec<App{name,cpu_pct,mem_bytes}>, uptime_s: Option<f64>}>`, `banner: Vec<{text,since,priority}>`, `host{name}`, `now: String`); unknown fields ignored, missing ones default.
  - `format::{gb(bytes: u64) -> String, uptime(s: f64) -> String, clock(iso: &str) -> String, level(v: Option<f64>, warn: f64, bad: f64) -> Level, app_cpu(pct: f64) -> String}`
  - `view::{Level{Ok,Warn,Bad}, Theme{Light,Dark}, GaugeVm{fill_pct: f32, text: String, unit: &'static str, level: Level, detail: String}, DiskVm{label, used_text, fill_pct: f32, level: Level, mounted: bool}, AppVm{name, cpu_text, mem_text}, BannerVm{text, time, bad: bool}, ViewModel{blank: bool, theme: Theme, keep: f32, host: String, meta: String, banners: Vec<BannerVm>, cpu: GaugeVm, ram: GaugeVm, temp: GaugeVm, disks: Vec<DiskVm>, apps: Vec<AppVm>}, view_model(d: &ScreenData) -> ViewModel, ViewModel::empty() -> ViewModel}` (`keep` = brightness / 100; `apps` empty ⇒ the UI shows `No app data yet`; host empty ⇒ `Command Center`).

- [ ] **Step 1: Write the failing tests** — exact values (they are what the reference shots show):

```rust
let vm = view_model(&load("dark.json"));
assert_eq!((vm.cpu.text.as_str(), vm.cpu.level), ("8", Level::Ok));
assert_eq!((vm.ram.text.as_str(), vm.ram.detail.as_str()), ("57", "8.4 / 14.6 GB"));
assert_eq!((vm.temp.text.as_str(), vm.temp.detail.as_str()), ("55", "· drive 48°"));
assert!((vm.temp.fill_pct - 35.0).abs() < 0.01);              // ring spans 30–100 °C
assert_eq!(vm.disks.iter().map(|d| d.used_text.as_str()).collect::<Vec<_>>(),
           ["459 / 904 GB", "689 / 916 GB", "73.6 / 234 GB"]);
assert_eq!((vm.apps[0].name.as_str(), vm.apps[0].cpu_text.as_str(), vm.apps[0].mem_text.as_str()),
           ("Paperclip", "2.3%", "2.2 GB"));
assert_eq!(vm.apps[2].mem_text, "0.1 GB");
assert!(vm.meta.ends_with("5:12 PM") && vm.meta.starts_with("up "));
assert_eq!((vm.theme, vm.keep, vm.blank), (Theme::Dark, 1.0, false));
let b = view_model(&load("banner.json")).banners;
assert_eq!((b[0].text.as_str(), b[0].time.as_str(), b[0].bad, b[1].bad), ("Internet down", "5:12 PM", true, false));
// format
assert_eq!([uptime(30.0), uptime(600.0), uptime(5.0*3600.0), uptime(86400.0), uptime(2.0*86400.0)],
           ["up 1 min", "up 10 min", "up 5 h", "up 1 day", "up 2 days"]);
assert_eq!([clock("2026-11-09T00:05:00-08:00"), clock("2026-11-09T12:00:00-08:00"), clock("bad")], ["12:05 AM", "12:00 PM", ""]);
assert_eq!([gb(100 * GIB), gb(GIB * 3 / 2)], ["100", "1.5"]);
assert_eq!([app_cpu(2.34), app_cpu(12.5)], ["2.3%", "13%"]);
assert_eq!([level(Some(79.9), 80.0, 95.0), level(Some(80.0), 80.0, 95.0), level(Some(95.0), 80.0, 95.0), level(None, 80.0, 95.0)],
           [Level::Ok, Level::Warn, Level::Bad, Level::Ok]);
```
  Plus edge tests: `stats: null` → all gauges `–`, fill 0, no disks, no apps; `temps.cpu_c` missing → temp `–`, detail from drive only; unmounted disk → `used_text == "not connected"`, `mounted == false`, fill 0; `power: "off"` → `blank == true`; `brightness` missing → `keep == 1.0`.
- [ ] **Step 2: Run** `cd display/screen && cargo test -p rack-screen-core` — Expected: FAIL (unresolved items).
- [ ] **Step 3: Implement** the three modules. Rounding = `f64::round` (matches JS `Math.round` for these positives); `gb` = one decimal under 100 GiB, else whole.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat(screen): core view model for the native rack screen`

### Task 3: core — theme palette, dimming, tweens

**Files:**
- Create: `display/screen/core/src/theme.rs`, `display/screen/core/src/tween.rs`
- Test: `display/screen/core/tests/theme.rs`

**Interfaces:**
- Produces: `theme::{Rgb(u8,u8,u8), Palette{bg,panel,line,text,muted,cpu,ram,temp,ok,warn,bad,banner_bad_bg,banner_warn_bg: Rgb}, palette(t: Theme) -> Palette, Palette::dim(&self, keep: f32) -> Palette, Palette::mix(&self, other: &Palette, t: f32) -> Palette, BLACK: Rgb}`; `tween::{Tween{from: f32, to: f32, start_ms: f64}, Tween::value(&self, now_ms: f64) -> f32, Tween::done(&self, now_ms: f64) -> bool, Tween::retarget(&mut self, to: f32, now_ms: f64), DURATION_MS: f64 = 300.0, ease_out(t: f32) -> f32}` (CSS `ease-out` = cubic-bezier(0,0,0.58,1), solved numerically).

- [ ] **Step 1: Failing tests**: `palette(Theme::Dark).cpu == Rgb(0x4c,0xc2,0xff)`, `palette(Theme::Light).bg == Rgb(0xee,0xf1,0xf4)` (all 13 tokens per theme copied from the CSS); `Rgb(0x4c,0xc2,0xff)` dimmed at 0.5 == `Rgb(38,97,128)`; dim at 1.0 is identity; `mix(t=0)` = self, `mix(t=1)` = other; tween: `value(start) == from`, `value(start+300) == to`, `value(start+150)` > linear midpoint (ease-out), `done` true from 300 ms; `retarget` mid-flight starts from the current value (no jump).
- [ ] **Step 2: Run** `cargo test -p rack-screen-core` — Expected: FAIL.
- [ ] **Step 3: Implement.** Dimming = each channel × keep, rounded.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(screen): palette, brightness dimming and tweens`

### Task 4: core — SSE parser and staleness

**Files:**
- Create: `display/screen/core/src/sse.rs`, `display/screen/core/src/stale.rs`
- Test: `display/screen/core/tests/sse.rs`

**Interfaces:**
- Produces: `sse::{SseParser::default(), SseParser::feed(&mut self, chunk: &str) -> Vec<SseEvent>, SseEvent{event: String, data: String}}`; `stale::{Staleness::new(start_ms: f64), Staleness::touch(&mut self, now_ms: f64), Staleness::is_stale(&self, now_ms: f64) -> bool, STALE_MS: f64 = 10_000.0}` (stale when > 10 s since the last push, or since start before any push — today's behavior).

- [ ] **Step 1: Failing tests**: one `event: screen\ndata: {…}\n\n` → one event; the same split across 3 chunks at arbitrary byte positions → one event only after the blank line; `\r\n` line endings; two `data:` lines joined with `\n`; `: keepalive` comment lines ignored; event without `event:` → `"message"`. Staleness: not stale at 9.9 s, stale at 10.1 s, `touch` resets.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(screen): SSE parser and staleness`

### Task 5: app — render the screen from a fixture, in the image

**Files:**
- Create: `display/screen/app/Cargo.toml` (bin `rack-screen`; `gpui = "0.2.2"`, path dep on core), `display/screen/app/src/main.rs`, `display/screen/app/src/ui/{mod,screen,gauge,panels,banner}.rs`
- Modify: `display/Dockerfile` (multi-stage, from the spike's), `display/run.sh` (cage runs `screen-loop.sh`; wait URL becomes `http://127.0.0.1:8787/api/screen/data`), `deploy.sh` (tag the old display image `led-notify-display:prev` before building)
- Create: `display/screen-loop.sh`
- Test: `tests/test_display_run.py`

**Interfaces:**
- Consumes: `view_model`, `ViewModel::empty`, `palette`, `Palette::{dim,mix}`, `Tween`.
- Produces: `rack-screen` reads env `RACK_SCREEN_FIXTURE` (path to a payload JSON → render it, no network) and `RACK_SCREEN_URL` (default `http://127.0.0.1:8787/api/screen/stream`, used in Task 6). `screen-loop.sh` runs `${SCREEN_BIN:-/usr/local/bin/rack-screen}` forever, logging each exit, `sleep 1` between runs.

- [ ] **Step 1: Failing pytest** in `tests/test_display_run.py`: `test_screen_loop_restarts_the_program` — run `display/screen-loop.sh` with `SCREEN_BIN` = a fake that appends to a log and exits 1; after 2.5 s the log has ≥ 2 runs and the loop is still alive. Update the fake `cage` to record its argv and add `assert "screen-loop.sh" in …` to `test_night_never_cuts_the_signal`.
- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_display_run.py` — Expected: the new/changed tests FAIL.
- [ ] **Step 3: Write `screen-loop.sh`, change `run.sh`** (keep monitor gating, outputs-on-only, SIGTERM handling, wait-for-led-notify). Dockerfile: builder copies the workspace, runs `cargo test -p rack-screen-core` and `cargo build --release -p rack-screen` with BuildKit cache mounts for the cargo registry and `target/`, `CARGO_BUILD_JOBS=3`; runtime as in the spike, no Chromium; copies `rack-screen`, `screen-loop.sh`, `run.sh`, `display/screen/fixtures/`. `deploy.sh`: `docker tag led-notify-display:local led-notify-display:prev` (if present) before the display build.
- [ ] **Step 4: Run** pytest — Expected: PASS (whole suite: `.venv/bin/pytest -q`).
- [ ] **Step 5: Build the UI**: root view = `ViewModel` + palette (dimmed by `keep`) laid out per the CSS grid (`1.3fr 1fr`, areas `top top / banner banner / gauges apps / disks apps`), sizes from `vw`/`vh` of the window; rings drawn in a GPUI `canvas` (track + arc from 12 o'clock clockwise, round caps, stroke 9 % of the ring box); Inter with tabular numbers; `blank` → paint `#000` only. Fixture mode only for now.
- [ ] **Step 6: Look check on the real monitor** — deploy, then for each fixture recreate the display container with `RACK_SCREEN_FIXTURE=/fixtures/<n>.json` set (a one-off compose override file on the server; never two `rack-screen` instances at once), screenshot with `WAYLAND_DISPLAY=wayland-0 grim /tmp/<n>.png` inside the container, copy to the Mac, compare side by side with `…-ref-<n>.png`.
  Expected: same layout, colors, sizes, fonts; differences only in anti-aliasing. Fix and repeat. Also render a fixture with a 30-char app name and `"used_bytes"` ≥ 1000 GB (Review Focus 5). If `grim` can't capture, ask the owner for a phone photo.
- [ ] **Step 7: Commit** `feat(screen): native GPUI rack screen (fixture mode) replaces Chromium in the image`

### Task 6: app — live feed, staleness, motion

**Files:**
- Create: `display/screen/app/src/feed.rs`
- Modify: `display/screen/app/src/main.rs`, `display/screen/app/src/ui/*.rs`

**Interfaces:**
- Consumes: `SseParser`, `Staleness`, `Tween`, `view_model`.
- Produces: `feed::spawn(url: String) -> async_channel::Receiver<ScreenData>` — a std thread: `ureq` GET with a 15 s read timeout, feeds lines to `SseParser`, sends each `screen` event's parsed payload; on any error waits 2 s and reconnects, forever. Unparseable payload: log once, skip.

- [ ] **Step 1: Wire it**: a GPUI foreground task receives payloads → `view_model` → retargets tweens (ring fills, disk fills, palette mix 0→1 on theme/brightness change; first payload applies with no animation) and notifies; a 2 s GPUI timer re-checks `Staleness` (stale → gauges/panels at 45 % opacity, ` · reconnecting` in warn after the meta). Request animation frames only while any tween is not `done`.
- [ ] **Step 2: Deploy and check live** (`./deploy.sh`): numbers match `curl -s http://127.0.0.1:8787/api/screen/data` on the server; move a brightness slider in `command.home.lab` → the 10 s preview shows; `sudo docker restart led-notify` → dimmed + reconnecting within ~10 s, back by itself; `sudo docker kill led-notify; sudo docker start led-notify` → same (Review Focus 2). Idle CPU in `docker stats` ≤ ~2 %.
- [ ] **Step 3: Night check**: set quiet hours to start 2 min from now in the UI → screen goes pure black, monitor stays awake; restore the owner's quiet hours (23:00–07:00) afterwards.
- [ ] **Step 4: Commit** `feat(screen): live feed, reconnect, motion`

### Task 7: remove the web screen; memory limit; docs

**Files:**
- Delete: `led_notify/web/static/screen/` (index.html, screen.css, screen.js)
- Modify: `led_notify/web/static/index.html` (remove the `rack screen` pill), `tests/test_screen_api.py` (`test_screen_page_served` → `test_screen_page_removed`), `docker-compose.yml` (display `mem_limit` = measured container peak × 2, rounded up to 64 MiB; comment says native GPUI, not Chromium), `README.md` (lines about `/screen/`, the display container, rollback: `sudo docker tag led-notify-display:chromium led-notify-display:local && sudo docker compose -f /DATA/AppData/led-notify/src/docker-compose.yml up -d display`)

- [ ] **Step 1: Failing test** `test_screen_page_removed`: `client.get("/screen/").status_code == 404`.
- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_screen_api.py` — Expected: FAIL (page still served).
- [ ] **Step 3: Delete and edit** as listed.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — Expected: all PASS.
- [ ] **Step 5: Deploy**, check the UI no longer shows the pill and the monitor still shows the screen.
- [ ] **Step 6: Commit** `feat: the rack screen is native only; web /screen/ removed`

### Task 8: final measurement and the day-long check

**Files:**
- Modify: `README.md` (measured numbers), skill `/Users/gustavogomez/.claude/skills/operating-zima-home-server/SKILL.md` (Command Center row: native GPUI screen, rollback tag, `measure.sh`)

- [ ] **Step 1: Measure** after ≥ 1 h live: `SETTLE_S=3600 … measure.sh cost`.
  Expected: `cost_mib ≤ 150`; record next to the Task 1 baseline.
- [ ] **Step 2: Day-long log**: on the server, `nohup` a loop running `measure.sh snapshot` every 10 min for 24 h into `/DATA/AppData/led-notify/screen-mem.log`. Expected after 24 h (checked in a later session; note it in the ledger and the owner's report): `container_mib` and `gtt_mib` flat (± 10 MiB).
- [ ] **Step 3: Update README + skill; commit** `docs: native rack screen numbers and rollback`
