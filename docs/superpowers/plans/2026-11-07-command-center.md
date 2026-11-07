# Command Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** led-notify grows a rack status screen (CPU, RAM, temps, disks, top 5 apps) shown on the server's own DisplayPort, with a day/night/quiet-hours power schedule, and is presented as "Command Center".

**Architecture:** led-notify (Python/FastAPI) gains a stats sampler, a screen schedule fed by the arbiter, a `/screen/` page and `/api/screen*` endpoints. A second container `display` in the same compose runs seatd + cage + Chromium kiosk on the DRM device and polls `/api/screen` to switch the output on/off with `wlr-randr`.

**Tech Stack:** Python 3.12, FastAPI, httpx (unix socket), pytest/pytest-asyncio; plain HTML/CSS/JS page; debian:trixie-slim + cage 0.2.0 + chromium + seatd + wlr-randr.

**Spec:** `docs/superpowers/specs/2026-11-07-command-center-design.md`

## Global Constraints

- Wake only for alerts the strip actually shows during quiet hours (rule `quiet_behavior: dim`); wake length `screen.wake_min` (default 5).
- Quiet-hours times come from the existing `quiet_hours` settings; no second copy.
- Never show PIDs or process command lines; app rows use friendly names.
- Display container: `--device /dev/dri /dev/tty0 /dev/tty1`, `cap_add: SYS_TTY_CONFIG`, `/run/udev:ro`, `WLR_BACKENDS=drm`, `WLR_RENDERER=pixman`, `WLR_LIBINPUT_NO_DEVICES=1`, no `/dev/input`, `WAYLAND_DISPLAY` unset for cage. Not privileged.
- led-notify must run unchanged in behavior when the display container is absent; no new hard dependency.
- Stats sampling every 2 s must stay cheap: cgroup files, not the Docker stats API; container list refreshed at most every 30 s.
- Names the owner sees say "Command Center"; container/package/repo names unchanged.

## Review Focus

1. A drive not mounted (Backup USB unplugged) → row says "not connected", no exception, other stats still render. → Task 4 test `test_disk_not_mounted`.
2. Docker socket unreachable / a container disappearing between list and read → apps list shrinks, sampler keeps running. → Task 4 test `test_apps_skip_vanished_container`.
3. Quiet hours spanning midnight with a wake at 06:58 → screen stays on through 07:00 (end) and continues in day mode, not off. → Task 2 test `test_wake_runs_into_morning`.
4. Settings changed while running (quiet start moved, quiet disabled) → `/api/screen` reflects it on the next call, no restart. → Task 2 test `test_quiet_disabled_never_off`.
5. Display page loses the SSE stream (led-notify redeploy) → page reconnects by itself and keeps last values meanwhile. → Task 5 (EventSource auto-reconnect + `stale` marker after 10 s), checked manually in Task 7.

---

### Task 1: `screen` settings + sunrise/sunset

**Files:**
- Modify: `led_notify/store/settings.py` (DEFAULT_SETTINGS, RANGES)
- Create: `led_notify/screen/__init__.py`, `led_notify/screen/sun.py`
- Test: `tests/test_screen.py`

**Interfaces:**
- Produces: settings `screen = {"wake_min": 5, "latitude": 34.05, "longitude": -118.24}`; RANGES `screen.wake_min (1, 60)`, `screen.latitude (-90, 90)`, `screen.longitude (-180, 180)`.
- Produces: `sun_times(day: date, lat: float, lon: float, tz: tzinfo) -> tuple[datetime, datetime] | None` (local sunrise, sunset; None in polar day/night).

- [ ] Tests: `test_screen_defaults` (defaults present, `update({"screen": {"wake_min": 0}})` raises SettingsError); `test_sun_times_la` — 2026-06-21 LA sunrise 05:42±3 min, sunset 20:08±3 min; 2026-12-21 sunrise 06:55±3, sunset 16:47±3 (America/Los_Angeles).
- [ ] Run, see fail. Implement (NOAA solar-position approximation, zenith 90.833°). Run, pass. Full suite green. Commit.

### Task 2: Screen schedule

**Files:**
- Create: `led_notify/screen/schedule.py`
- Test: `tests/test_screen.py`

**Interfaces:**
- Consumes: `in_window` (engine/quiet.py), `sun_times`, settings, clock (FakeClock in tests).
- Produces: `class ScreenSchedule(settings, clock)` with
  - `note_alert(name: str, rule: dict) -> None` — records `{name, time}` as the latest alert; if quiet hours are active now, sets wake-until = monotonic + wake_min*60.
  - `state() -> dict` = `{"power": "on"|"off", "theme": "light"|"dark", "quiet": bool, "awake_until": str|None, "last_alert": {"name", "time"}|None}`.
  - Rules: not quiet → on; theme light iff sunrise ≤ now < sunset (None from sun_times → dark). Quiet → on+dark while woken, else off.

- [ ] Tests: `test_day_light`, `test_evening_dark` (21:00), `test_quiet_off` (23:30), `test_wake_at_night` (02:00 note_alert → on/dark; +299 s on; +301 s off), `test_wake_restarts` (second alert at +4 min extends), `test_wake_runs_into_morning` (06:58 wake → 07:01 on, theme by sun), `test_quiet_disabled_never_off`, `test_day_alert_does_not_set_wake` (12:00 alert → last_alert set, awake_until None).
- [ ] Run fail → implement → pass → commit.

### Task 3: Arbiter tells the screen what it shows

**Files:**
- Modify: `led_notify/engine/arbiter.py` (constructor kwarg `on_shown=None`; call in `_play_oneshot` after "shown" log, and in `tick` when a new `("state", …)` base key is rendered with level > 0)
- Test: `tests/test_arbiter.py`

**Interfaces:**
- Produces: `Arbiter(..., on_shown: Callable[[str, dict], None] | None = None)`; called with `(event_name, live_rule)`; exceptions from it are logged, never raised.

- [ ] Tests: `test_on_shown_oneshot` (day oneshot → called once), `test_on_shown_quiet_dim_state` (23:30, `internet.down` start → called), `test_on_shown_not_for_suppressed` (23:30, `device.known_arrived` → not called), `test_on_shown_error_is_swallowed`.
- [ ] Run fail → implement → pass → full suite → commit.

### Task 4: Stats sampler

**Files:**
- Create: `led_notify/stats/__init__.py`, `led_notify/stats/host.py`, `led_notify/stats/apps.py`, `led_notify/stats/sampler.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- `host.py`: `cpu_times(path="/proc/stat") -> tuple[int, int]` (busy, total jiffies, first line); `cpu_pct(prev, cur) -> float`; `memory(path="/proc/meminfo") -> dict(used_bytes, total_bytes)` with used = MemTotal − MemAvailable; `uptime_s(path="/proc/uptime") -> float`; `disk(label: str, path: str) -> dict(label, mounted: bool, used_bytes, total_bytes, pct)` — `mounted` = `os.path.ismount(path)`; total = f_blocks·f_frsize, used = (f_blocks − f_bfree)·f_frsize, pct as `disk_usage_pct`.
- `apps.py`: `friendly_name(container: str) -> str` (built-in map: paperclip→Paperclip, jellyfin→Jellyfin, qbittorrent→qBittorrent, adguard-home→AdGuard, hermes→Hermes, browser→Brand browser, browser-mcp→Browser MCP, flaresolverr→FlareSolverr, nzbget→NZBGet, calibre-web-automated→Calibre-Web, audiobookshelf→Audiobookshelf, led-notify→Command Center, led-notify-display→Rack screen, gluetun→VPN, caddy→Home Lab Names, postgres→Postgres, seerr→Seerr, sonarr/radarr/lidarr/bazarr/prowlarr/shelfmark→capitalized, jslab-prod-*→JSLab site, jslab-staging-*→JSLab staging, jslab-tunnel→JSLab tunnel; else `-`/`_` → spaces, title case). `class AppMeter(cgroup_root: str, list_containers: Callable[[], Awaitable[list[dict]]], clock)`; `async sample() -> list[dict(name, cpu_pct, mem_bytes)]` sorted by cpu desc; cpu % of the whole machine = Δusage_usec / (Δt·1e6·ncpu)·100; mem = memory.current − inactive_file; reads `<root>/system.slice/docker-<id>.scope/`; missing files → container skipped; list refreshed every 30 s. `docker_list(socket) -> list[dict(id, name)]` via httpx `AsyncHTTPTransport(uds=socket)` GET `/containers/json`.
- `sampler.py`: `class StatsSampler(settings, clock, apps: AppMeter, disks: list[tuple[label, path]], interval_s=2.0)`; `async run()`; `latest: dict | None` = `{"cpu_pct", "memory", "temps": {"cpu_c", "drive_c"}, "disks": [...], "apps": top 5, "uptime_s"}`; each part failing independently → that part `None`, loop continues.

- [ ] Tests with tmp files: `test_cpu_pct`, `test_memory_used`, `test_disk_not_mounted`, `test_friendly_names`, `test_apps_top_by_cpu` (two fake scope dirs, two samples), `test_apps_skip_vanished_container`, `test_sampler_survives_part_failure`.
- [ ] Run fail → implement → pass → commit.

### Task 5: Screen API + page

**Files:**
- Create: `led_notify/screen/labels.py`, `led_notify/web/static/screen/index.html`, `screen.css`, `screen.js`
- Modify: `led_notify/web/api.py` (Services gains `screen`, `stats`; routes), `led_notify/app.py` (wire ScreenSchedule into Arbiter `on_shown`, StatsSampler task; disk list Main `/host/DATA`, Movies SSD `/host/DATA/Media/MoviesSSD`, Backup `/host/DATA/Backup`; cgroup root `/host/cgroup`; hostname from `/host/etc/hostname` falling back to `socket.gethostname()`)
- Test: `tests/test_api.py`

**Interfaces:**
- `labels.py`: `label(name: str) -> str` plain words (internet.down "Internet down", dns.down "DNS down", disk.smart_error "Drive health warning", internet.slow "Internet slow", disk.full "Disk almost full", cpu.hot "CPU hot", device.new_late "Unknown device joined", device.new "New device joined", container.died "An app stopped", internet.up "Internet back", device.known_arrived "Someone arrived", adguard.block_spike "Blocking spike"; else name with `.`/`_` → spaces, capitalized).
- `GET /api/screen` → `ScreenSchedule.state()` (used by the display container).
- `GET /api/screen/data` → `{"screen": state, "stats": sampler.latest, "banner": [{"text","since"}…], "host": {"name"}, "now": iso}`; banner = active states (arbiter snapshot, labels) + last_alert if within wake_min minutes and not already an active state.
- `GET /api/screen/stream` → SSE `event: screen` with the same payload every 2 s.
- Page: layout A from `docs/superpowers/specs/assets/2026-11-07-command-center-look.html`; `data-theme` from payload; light + dark tokens; sizes in `vw/vh` so 1366×768 to 4K read the same; no animations except gauge transitions ≤300 ms; banner row on top when non-empty; drives not mounted show "not connected"; marks stale (dims numbers) after 10 s without data; EventSource reconnects by itself.

- [ ] Tests: `test_screen_endpoint_power`, `test_screen_data_shape` (stats None before first sample is OK), `test_banner_from_active_state`, `test_labels`.
- [ ] Run fail → implement → pass. Render check: run dev server with `--fake-strip --no-monitors`, screenshot `/screen/` at 1920×1080 in both themes, look at it. Commit.

### Task 6: Display container + Command Center naming

**Files:**
- Create: `display/Dockerfile`, `display/run.sh`
- Modify: `docker-compose.yml` (service `display`, container `led-notify-display`, image `led-notify-display:local`, `network_mode: host`, devices/caps/env per Global Constraints, `restart: unless-stopped`, `depends_on` none; led-notify gains `/sys/fs/cgroup:/host/cgroup:ro` and `/etc/hostname:/host/etc/hostname:ro`; x-casaos title "Command Center", tagline "LED strip alerts + rack status screen", hostname `command.home.lab`), `deploy.sh` (also builds `display/`), `led_notify/web/static/index.html` (title/header "Command Center" + link to `/screen/`), `README.md`.

**run.sh behavior:** start `seatd -g root`; loop forever: (re)start `cage -- chromium --kiosk --no-sandbox --ozone-platform=wayland --disable-gpu --no-first-run --noerrdialogs --disable-infobars --disable-extensions --disable-background-networking --disable-component-update --renderer-process-limit=1 --user-data-dir=/tmp/chromium http://127.0.0.1:8787/screen/` if not running; every 5 s GET `http://127.0.0.1:8787/api/screen`; when `power` differs from what was applied (or outputs changed, i.e. hot-plug), run `WAYLAND_DISPLAY=wayland-0 wlr-randr --output <each> --on|--off`; if led-notify is unreachable for 60 s at night (local time within 23:00–07:00 fallback) turn outputs off; log state changes only.

- [ ] Build both images on the server via `./deploy.sh`; `docker ps` shows both healthy/running; `/api/screen` answers; display logs show cage running with 0 outputs (no monitor yet).
- [ ] Commit.

### Task 7: Address, verification, docs

**Files:**
- Modify (proxy repo `HomeLab/proxy`): Caddyfile adds `command.home.lab` → 127.0.0.1:8787; `tools/directory.py` label "Command Center"; `./deploy.sh` + `./check.sh` there.
- Modify: dashboard tile (stored compose + `systemctl restart zimaos-app-management`, per skill), server skill `operating-zima-home-server`, memory.

- [ ] `curl http://command.home.lab/api/screen` and `http://lights.home.lab/` both 200.
- [ ] Kill test: `docker stop led-notify-display` → `/api/state` still fine; start again.
- [ ] Night logic check on the live service: temporarily set quiet hours to cover now, POST a `critical` test event, `/api/screen` → on/dark, after wake → off; restore settings.
- [ ] Full test suite green; whole-branch review; merge to main; push.
- [ ] Owner: plug in the monitor and confirm picture, night off, wake (needs the monitor).
