# led-notify — "Command Center"

Homelab notifications on a BanlanX SP548E Bluetooth LED strip, plus a status screen on the rack monitor
(the server's own DisplayPort), running as one ZimaOS app shown as **Command Center**.

- UI: http://command.home.lab (old address http://lights.home.lab, or http://192.168.4.61:8787)
- Rack screen: drawn natively on the rack monitor (no web page); preview it on a Mac with `display/screen` (below)
- Webhook: `curl -X POST http://192.168.4.61:8787/api/events -H 'Content-Type: application/json' -d '{"name":"backup.done","pattern":"green_flash"}'`
  - optional: `"kind": "start" | "clear"` for persistent states, `"priority": "low|normal|high|critical"`,
    `"effect": 1-146` or `"color": [r,g,b]` instead of `"pattern"`, `"duration": seconds`
  - if a webhook token is set in the UI, add `-H 'Authorization: Bearer <token>'`

## Develop

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/python -m led_notify.app --fake-strip --no-monitors --data-dir /tmp/led-notify-dev
```

## Deploy

```bash
./deploy.sh                                              # sync, build on the server, (re)start
.venv/bin/python -m led_notify.smoke --url http://192.168.4.61:8787   # watch every pattern
```

The strip accepts one Bluetooth connection: while the service runs, the BanlanX phone app can't connect.
Design: `docs/superpowers/specs/2026-10-11-led-notify-design.md`; rack screen: `docs/superpowers/specs/2026-11-07-command-center-design.md`, native screen: `docs/superpowers/specs/2026-11-09-native-rack-screen-design.md`.

## Rack screen

Two containers, one app (`docker-compose.yml`):
- `led-notify` samples CPU, memory, temperatures, the three drives and per-app CPU/memory (host cgroups,
  mounted read-only at `/host/cgroup`) every 2 s and serves `/api/screen` (power + theme),
  `/api/screen/data` and `/api/screen/stream` (SSE).
- `led-notify-display` (`display/`) runs `seatd` + `cage` on `/dev/dri` (needs `SYS_TTY_CONFIG`, `/dev/tty0`,
  `/dev/tty1`, `/run/udev`); cage's only client is `screen-loop.sh`, which keeps `rack-screen` running — a Rust
  program (`display/screen`, GPUI, Vulkan on the Vega iGPU) that reads `/api/screen/stream` and draws the screen.
  It replaced a Chromium kiosk (~275 MiB) in Nov 2026. `run.sh` keeps every output enabled with `wlr-randr` and
  never turns one off: the rack monitor falls asleep when its signal goes away and only a power cycle (unplug its
  USB power) wakes it again, so "off" is pure black drawn by `rack-screen` (the backlight stays lit). If
  `rack-screen` dies, only it restarts; cage (and the signal) stays. Nothing starts until a DRM connector reports
  `connected` (with no monitor, cage spins a CPU core). `docker logs led-notify-display` says
  `waiting for a monitor…`, `kiosk started`, `output … on`, `rack-screen exited …`.

Schedule (from the Rules → Settings page): on outside quiet hours — light theme between local sunrise and
sunset (location under "Rack screen"), dark otherwise; black during quiet hours, except that any alert the
strip shows at night (rule quiet behavior `dim`, i.e. high/critical) wakes it for "Wake at night for (min)"
(default 5). Test it without noise: `curl -X POST …/api/events -d '{"name":"screen.test","priority":"high","pattern":"white_flash","duration":1,"sound":""}'`.
Brightness: two sliders under "Rack screen" (light look / dark look, 10–100 %, saved on release, on the
monitor within ~2 s). Moving a slider saves as it moves (at most every 300 ms) and shows that slider's look
(light/dark, on even in quiet hours) for 10 s after the last move, so either look can be tuned at any hour.
The panel (EDID `HCT` "HDMI") does not answer DDC/CI, so the backlight stays at full and
`rack-screen` mixes all its colors toward black instead (`Palette::dim` in `display/screen/core`).
Stopping `led-notify-display` never affects the strip; the console text returns to the monitor while it is stopped.

Working on the screen (`display/screen`: `core` = payload, formatting, colors, motion, SSE — plain Rust;
`app` = the GPUI program):
- Tests: `cd display/screen && cargo test -p rack-screen-core` (also run by the image build).
- Preview on a Mac at the monitor's size with a fixed payload (the reference shots are in
  `docs/superpowers/specs/assets/2026-11-09-rack-screen-ref-*.png`, payloads in `display/screen/fixtures/`):
  `cargo build --features gpui/runtime_shaders -p rack-screen && RACK_SCREEN_WINDOW=800x480 RACK_SCREEN_FIXTURE=fixtures/dark.json target/debug/rack-screen`
- Screenshot of the real monitor: `sudo docker exec -e WAYLAND_DISPLAY=wayland-0 led-notify-display grim /tmp/s.png`.
- Memory: `ssh zima 'bash -s' < display/measure.sh` (snapshot) or `… cost` (stops the screen, measures the
  difference incl. GPU memory; 10 min).
- Rollback: each deploy keeps the previous screen as `led-notify-display:prev`; the Chromium version is kept as
  `led-notify-display:chromium`. `sudo docker tag led-notify-display:<tag> led-notify-display:local && sudo docker compose -f /DATA/AppData/led-notify/src/docker-compose.yml up -d display`.
- `Cargo.lock` started from gpui 0.2.2's own lock and builds `--locked`: the newest `libc` breaks gpui's
  `xattr` 0.2.3.

Note: `./deploy.sh` always (re)creates the container with `docker compose -f /DATA/AppData/led-notify/src/docker-compose.yml up -d --force-recreate`,
so repo compose edits take effect. `casaos-cli app-management install` only registers the ZimaOS dashboard tile (it creates no container),
so it runs once when `/var/lib/casaos/apps/led-notify` is missing; afterwards the synced compose is copied over the registered one.
All SSH steps share one multiplexed connection.

Before syncing, `./deploy.sh` copies `/DATA/AppData/led-notify/data/settings.json` and `devices.json` to
`<name>.pre-deploy-<UTC timestamp>` next to them (newest 5 of each kept), because older code treats a
`settings.json` saved by newer code as invalid and falls back to defaults.
Rollback = deploy the old commit, then restore the matching `settings.json.pre-deploy-*`.
