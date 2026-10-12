# led-notify — Design Spec

**Date:** 2026-10-11
**Status:** Draft for review

## 1. Purpose

A glanceable ambient notifier for the home lab. A BanlanX **SP548E** Bluetooth LED
strip near the ZimaOS server shows network, device and server events as light
patterns, so problems are visible without opening a dashboard. It runs 24/7 as a
ZimaOS custom app with a small web UI for testing patterns, manual control and
configuration, plus a webhook API so other tools can trigger lights.

**Success criteria**
- When the internet drops, the strip shows Red Breath within ~30 s and flashes green when it returns.
- An unknown device joining the LAN plays Blue Comet Spin → Green Comet Spin within ~60 s.
- Every event and pattern can be tested from the web UI, and the result on the strip matches a real event.
- Any LAN tool can trigger a pattern with one HTTP POST.
- The service survives strip power loss, server reboots and ZimaOS updates without manual intervention.

## 2. Environment (verified 2026-10-11)

| Item | Value |
|---|---|
| Server | ZimaOS v1.7.1, host `GomezPalacios`, 192.168.0.61/24, gateway 192.168.0.1 |
| Bluetooth | Intel Wireless-AC 9260 (`hci0`), `bluetoothd` active, D-Bus at `/run/dbus/system_bus_socket` |
| Strip | `SP548E`, BLE address `A6:15:50:62:0A:9D`, 6xx protocol, firmware V3.x (status layout in `banlanx_ctl.py`) |
| Disks | One NVMe `nvme0n1` (Crucial P3 1 TB); `/DATA` is `nvme0n1p8` (ext4) |
| CPU temp | hwmon `k10temp` (`Tctl`) — AMD |
| Docker | 27.5.1; socket `/var/run/docker.sock`; existing app `adguard-home` (bridge network, DNS on host :53, UI on host :3001) |
| DHCP | Router 192.168.0.1 (AdGuard is not the DHCP server) |
| Port | 8787 free — used for led-notify |

**Strip capabilities:** solid colour (RGB + level), 146 firmware dynamic effects
(`SIXXX_EFFECTS_DYNAMIC`), 18 sound effects, brightness 0–255, speed 1–10, length.
No per-LED addressing. One BLE connection at a time (the phone app is locked out
while the service is connected). Writes require acknowledgement; each command is
1–2 frames with a 50 ms gap, so the minimum practical step for software sequences
is **150 ms**.

## 3. Architecture

Single Python 3.12 container (approach A), host networking, installed as a ZimaOS
custom app.

```
monitors/* ──events──▶ engine.Arbiter ──Pattern──▶ engine.Player ──commands──▶ driver.StripLink ──BLE──▶ SP548E
web (webhook, UI test) ──events──▶   ▲
web (manual control) ──manual state──┘
store/ (settings.json, devices.json)  ◀──▶  web + arbiter + monitors
```

### 3.1 Units

| Unit | Responsibility | Depends on |
|---|---|---|
| `driver/banlanx_ctl.py` | Vendored, unchanged copy of `/Users/gustavogomez/led-strip/banlanx_ctl.py` (protocol + `Strip`). | `bleak` |
| `driver/link.py` — `StripLink` | Owns the single long-lived BLE connection. Connects by address, reconnects with backoff (2 s doubling to 60 s cap), serialises commands through an `asyncio.Queue`, sends a status query every 30 s as keepalive/health, exposes `connected`, `rssi`, `last_error`. On reconnect, re-applies the last requested `Look`. Implements the `StripPort` interface. | `banlanx_ctl.Strip` |
| `driver/fake.py` — `FakeStrip` | `StripPort` implementation that records commands; used by tests and by `--fake-strip` dev mode. | — |
| `patterns.py` | Pattern catalogue. A `Pattern` is either `Effect(fx, speed, brightness)` (firmware effect, held indefinitely) or `Sequence(steps, repeat)` where a step is `Solid(rgb, ms)`, `Effect(fx, ms)` or `Off(ms)` (min 150 ms). Named built-ins listed in §4.3. Also resolves ad-hoc webhook looks (`effect` number, `color` RGB). | — |
| `engine/player.py` — `Player` | Plays one `Pattern` on a `StripPort`: sends an effect once, or steps through a sequence with timing; cancellable; reports completion. Applies brightness scaling (global max, quiet-hours factor). | `StripPort`, `patterns` |
| `engine/arbiter.py` — `Arbiter` | Single authority for what the strip shows. Holds active states, the one-shot queue, manual override, quiet-hours logic and dedup; decides the current `Look`; drives `Player`; appends outcomes to the event log; publishes state changes to subscribers. Takes a `Clock` for testability. | `Player`, `store`, `Clock` |
| `monitors/*.py` | Independent async loops that emit `Event`s via a callback. Each has injectable probes (ping, ARP, DNS, statvfs, smartctl, hwmon reader, docker event stream, AdGuard client) and a `Clock`. Debounce logic lives in pure, testable classes. | probes, `Clock` |
| `monitors/supervisor.py` | Starts monitors, restarts a crashed monitor with backoff (5 s doubling to 5 min), tracks per-monitor health (`ok` / `error: <msg>` / `disabled`). | monitors |
| `store/` | `SettingsStore` (rules, thresholds, quiet hours, idle glow, brightness, AdGuard credentials, webhook token) and `DeviceStore` (seen devices). JSON files under `/data`; atomic writes (temp file + `os.replace`); malformed file → defaults + warning flag surfaced in UI. Secrets are write-only through the API. | — |
| `web/` | FastAPI app: REST API, SSE stream, static single-page UI (`web/static/index.html`, vanilla JS + CSS, no build step). | arbiter, stores, supervisor, patterns |
| `app.py` | Wires everything, runs Uvicorn on `0.0.0.0:8787`. Flags: `--fake-strip`, `--data-dir`. | all |

### 3.2 Event model

```python
Event(name: str, kind: "oneshot" | "start" | "clear", source: str,
      priority: str | None = None, look: LookSpec | None = None,
      duration_s: float | None = None, data: dict = {}, force: bool = False)
```

- `start` / `clear` manage a **persistent state** keyed by `name` (e.g. `internet.down`).
  A `clear` may be configured to emit a follow-up one-shot (e.g. `internet.down` clear → `internet.up` one-shot).
- `oneshot` plays once (pattern's natural length, or `duration_s` for held effects; default 5 s, max 60 s).
- `priority` and `look` from the event are used only when no rule exists for `name` (webhook ad-hoc events).
- `force` (UI test only) bypasses priority comparison, quiet hours and dedup.

## 4. Pattern engine and arbitration

### 4.1 Priorities

`critical (4) > high (3) > manual (2.5) > normal (2) > low (1) > idle (0)`

### 4.2 Arbitration rules

1. **Base look** = highest-priority active persistent state (ties: most recently started); else manual override if active; else idle glow.
2. A **one-shot** is enqueued if its rule is enabled and passes quiet-hours filtering. When it reaches the head of the queue it plays only if `priority >= base look priority`; otherwise it is logged as `skipped:priority`.
3. Queue is FIFO, max length 5; overflow drops the oldest pending entry (logged `dropped:queue_full`).
4. **Dedup:** the same one-shot `name` (+ `data.key` if present, e.g. MAC) within 30 s is logged `skipped:duplicate`.
5. After a one-shot finishes (or is preempted), the current base look is re-rendered.
6. A new persistent state with priority higher than a playing one-shot **preempts** it immediately.
7. **Manual override** (from UI) sets the base look at priority `manual`, expires after `manual_timeout_min` (default 30), or on "Resume auto". `high`/`critical` states and one-shots still take over; manual resumes after they clear if not expired.
8. **Quiet hours** (default 23:00–07:00, local time, configurable, can span midnight): events whose rule `quiet_behavior` is `suppress` are logged `skipped:quiet`; `dim` events render at `quiet_brightness_pct` (default 20 %). Idle glow is off during quiet hours (configurable). Defaults: `critical`/`high` rules → `dim`; others → `suppress`.
9. **Idle glow:** solid colour (default teal `#00B3A4`) at `idle_brightness` (default 12/255).
10. Every rendered look is scaled by `max_brightness` (default 255).

### 4.3 Default rules

| Event | Kind | Priority | Pattern (named) | Definition |
|---|---|---|---|---|
| `internet.down` | state | critical | `red_breath` | Effect 0x62 Red Breath, speed 5 |
| `dns.down` | state | high | `purple_breath` | Effect 0x67 Purple Breath, speed 5 |
| `disk.smart_error` | state | high | `red_white_snake` | Effect 0x7B Red/White Snake |
| `internet.slow` | state | normal | `yellow_breath` | Effect 0x65 Yellow Breath, speed 3 |
| `disk.full` | state | normal | `yellow_stacking` | Effect 0x6C Yellow Stacking |
| `cpu.hot` | state | normal | `fire_red_yellow` | Effect 0x09 Red/Yellow Fire |
| `device.new_late` | oneshot | high | `intruder` | Effect 0x77 Red/Blue/White Snake, 8 s |
| `device.new` | oneshot | normal | `new_device` | Effect 0x7F Blue Comet Spin 4 s → Effect 0x7E Green Comet Spin 4 s |
| `container.died` | oneshot | normal | `purple_flash_x3` | (Solid purple 300 ms, Off 200 ms) ×3 |
| `internet.up` | oneshot | normal | `green_flash_x2` | (Solid green 400 ms, Off 250 ms) ×2 |
| `device.known_arrived` | oneshot | low | `welcome` | Effect 0x68 White Breath, 6 s |
| `adguard.block_spike` | oneshot | low | `yellow_white_stars` | Effect 0x5E Yellow/White Background Stars, 5 s |
| *(webhook, no rule)* | per event | event's or `normal` | event's `pattern`/`effect`/`color`, else `white_flash` | (Solid white 300 ms, Off 200 ms) ×2 |

Additional named patterns available to rules/webhook: `green_flash`, `red_flash_x3`,
`blue_flash`, `rainbow`, `red_comet`, `green_comet`, `blue_comet`, plus every firmware
effect addressable by number. Exact effect numbers are confirmed against
`SIXXX_EFFECTS_DYNAMIC` during implementation and in the hardware smoke test.

Each rule: `{enabled, priority, pattern, duration_s?, quiet_behavior: dim|suppress, clear_followup?: event_name}`. For a one-shot whose pattern is a single `Effect`, the effect is held for `duration_s` (durations in the table above; default 5 s, max 60 s). `Sequence` patterns run their own length and ignore `duration_s`.

## 5. Monitors

All intervals/thresholds are settings with the defaults below. Debouncers are pure
classes fed `(timestamp, sample)` and unit-tested with a fake clock.

| Monitor | Probe | Interval | Start | Clear |
|---|---|---|---|---|
| `internet` | ICMP ping 1.1.1.1, 8.8.8.8, 9.9.9.9 (1 s timeout each) + gateway 192.168.0.1 | 10 s | `internet.down`: 3 consecutive rounds with all targets failing; `data.gateway_reachable` recorded | 2 consecutive rounds with ≥1 target OK → clear, follow-up `internet.up` |
| | | | `internet.slow`: over a 5 min window, median RTT > 150 ms or loss > 20 %, while not down | Within limits for 5 min |
| `dns` | DNS A query for `cloudflare.com` to 127.0.0.1:53, 2 s timeout | 30 s | `dns.down`: 3 consecutive failures | 2 consecutive successes |
| `devices` | ARP scan 192.168.0.0/24 (`arp-scan --localnet`, OUI vendor lookup) | 60 s | `device.new` for a MAC never seen (or `device.new_late` if local time 00:00–06:00), `data.key = MAC`; `device.known_arrived` for a device flagged `announce_arrival` reappearing after > 30 min absent | — |
| `disk` | `statvfs` on `/host/DATA`; `smartctl -H /dev/nvme0` | usage 5 min, SMART 60 min | `disk.full` at ≥ 90 %; `disk.smart_error` on SMART FAIL | < 85 %; SMART PASS |
| `thermal` | hwmon sensor named `k10temp` (fallback `coretemp`), `temp1_input` | 30 s | `cpu.hot`: ≥ 85 °C sustained 2 min | < 75 °C |
| `docker` | Docker event stream (`/var/run/docker.sock`) | stream | `container.died` (one-shot, `data.key = container name`) on `die` with non-zero exit code, unless a `stop`/`kill`/`restart` for that container was seen in the preceding 15 s | — |
| `adguard` | `GET http://127.0.0.1:3001/control/querylog?response_status=blocked` with basic auth; count entries in last 60 s | 60 s | `adguard.block_spike` when count > 50 and > 3× the rolling 1 h per-minute average | — |

- **Device baseline:** on first run (empty `devices.json`) the first completed scan records all devices silently. Subsequent scans alert only on new MACs.
- **Device record:** `{mac, ip, vendor, name, known, announce_arrival, private_mac (locally-administered bit), first_seen, last_seen}`; online = seen in last 3 scans.
- **AdGuard monitor** is `disabled` until credentials are set.
- **Monitor crash:** supervisor restarts with backoff; health shown in UI; never drives the strip.

## 6. Web UI and API

### 6.1 UI (`http://192.168.0.61:8787`)

Single page, vanilla JS, live via SSE; works at phone width; light/dark.

- **Header:** strip link status (connected / reconnecting attempt N / offline + last error), RSSI, current look + reason (`Red Breath ← internet.down since 22:41`), active states, monitor health dots, settings-file warning if any.
- **Test:** a button per rule event (fires through the arbiter; "force" toggle), and a searchable gallery of named patterns + all firmware effects with "preview on strip" (preview = forced one-shot, 8 s).
- **Manual:** colour picker, brightness, speed, effect select, power, **Resume auto**, expiry countdown.
- **Devices:** table (name, MAC, IP, vendor, private-MAC badge, online, first/last seen); rename, known toggle, announce-arrival toggle, forget; filter unknown/online.
- **Rules:** per-event enabled/priority/pattern/quiet behaviour; thresholds; quiet-hours window; idle colour/brightness; max brightness; manual timeout; AdGuard URL/username/password (write-only); webhook token (write-only, optional).
- **Log:** last 200 entries `{time, event, source, outcome: shown|skipped:priority|skipped:quiet|skipped:duplicate|skipped:disabled|dropped:queue_full|preempted}`.

### 6.2 API

| Method + path | Purpose |
|---|---|
| `POST /api/events` | Body: `{name, kind?="oneshot", priority?, pattern? \| effect? \| color?, duration?, data?}`. Returns `{accepted, outcome}`. Requires `Authorization: Bearer <token>` iff a webhook token is configured. |
| `POST /api/test/{name}` | UI test of a rule event; `?force=true`. |
| `POST /api/preview` | Body `{pattern \| effect \| color}`; forced 8 s one-shot. |
| `GET /api/state` | Current look, reason, active states, queue, manual override, strip + monitor health. |
| `POST /api/strip` | Manual: `{power?, color?, effect?, brightness?, speed?}` → sets manual override. |
| `DELETE /api/strip/manual` | Resume auto. |
| `GET/PUT /api/rules` | Rules table. |
| `GET/PUT /api/settings` | Settings (secrets returned as `"set": true/false` only). |
| `GET /api/devices`, `PATCH /api/devices/{mac}`, `DELETE /api/devices/{mac}` | Device list / edit / forget. |
| `GET /api/patterns` | Named patterns + firmware effect list. |
| `GET /api/log` | Event log. |
| `GET /api/stream` | SSE: `state`, `log`, `devices` messages. |

Validation errors → 422 with field messages. Unknown pattern/effect → 422.

## 7. Deployment

- **Source:** `HomeLab/led-notify/` (this repo). Layout: `led_notify/` package, `tests/`, `Dockerfile`, `docker-compose.yml`, `deploy.sh`.
- **Image:** `python:3.12-slim` + `arp-scan`, `smartmontools`, `iputils-ping`; Python deps `bleak`, `fastapi`, `uvicorn`, `dbus-fast` (via bleak), `httpx`, `dnspython`, `docker` (SDK). Built on the server: `deploy.sh` rsyncs the source to `/DATA/AppData/led-notify/src` and runs `sudo docker build -t led-notify:local`.
- **Compose** at `/DATA/AppData/led-notify/docker-compose.yml` with `x-casaos` metadata (title, icon, port 8787) so it appears on the ZimaOS dashboard:
  - `network_mode: host`, `restart: unless-stopped`
  - `cap_add: [NET_RAW, NET_ADMIN, SYS_RAWIO]`
  - `devices: [/dev/nvme0]`
  - volumes: `/run/dbus/system_bus_socket` (rw), `/var/run/docker.sock` (ro), `/DATA` → `/host/DATA` (ro), `/sys/class/hwmon` (ro), `/DATA/AppData/led-notify/data` → `/data` (rw)
  - env: `STRIP_ADDRESS=A6:15:50:62:0A:9D`, `TZ` = server timezone
- **Bluetooth access** goes through the host `bluetoothd` over D-Bus; the container does not run its own Bluetooth stack. If D-Bus policy denies the container, fall back to running the container as root with the socket mounted (root inside is already required for raw sockets).
- **Security note:** Docker socket access makes this container root-equivalent on the host (accepted). No UI auth (LAN only); optional webhook bearer token.

## 8. Error handling

- **BLE:** link loss → reconnect with backoff; on reconnect re-apply current look; `StripLink.send` while disconnected updates the desired look only (no exception to callers). Device not found (phone app holding it, strip unpowered) is reported in UI and retried.
- **Arbiter:** player exceptions are caught, logged, and the arbiter falls back to re-rendering the base look on the next tick.
- **Restart:** persistent states are not persisted; monitors re-derive them within one cycle. Devices and settings persist.
- **Storage:** atomic writes; malformed JSON → defaults in memory, original file renamed `*.corrupt-<ts>`, UI warning.
- **Monitors:** probe errors count as failed samples where meaningful (internet, dns) and as monitor errors otherwise (disk, adguard), never as false alerts.

## 9. Testing

- **TDD** with `pytest` + `pytest-asyncio`.
- **Unit:** arbiter (priority selection, one-shot gating, restore after one-shot, preemption, FIFO + overflow, dedup window, manual override + expiry, quiet-hours incl. midnight span, brightness scaling, clear follow-ups); player (sequence timing with fake clock, cancellation); each debouncer (internet down/up/slow, dns, disk hysteresis, thermal sustain, docker intentional-stop filter, adguard spike, device baseline/new/new_late/known_arrived); stores (atomic write, corrupt file recovery, secret masking); patterns (all named patterns resolve, min step 150 ms).
- **API:** FastAPI `TestClient` against `FakeStrip` + fake monitors: events webhook (token on/off), test/preview, manual, rules/settings round-trip, devices edit/forget, 422 paths.
- **Hardware smoke test:** `python -m led_notify.smoke` on the server plays every named pattern on the real strip with labels, for visual confirmation.
- **Deployed check:** after deploy, `GET /api/state` shows strip `connected`, all monitors `ok` (AdGuard `disabled` until credentials are set), and a webhook `curl` visibly plays.

## 10. Out of scope (v1)

UI sequence builder; sound-reactive modes; multiple strips; calendar/pomodoro integrations;
user authentication; persisting active states across restarts; IPv6 neighbour discovery.
