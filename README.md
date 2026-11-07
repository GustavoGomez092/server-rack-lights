# led-notify — "Command Center"

Homelab notifications on a BanlanX SP548E Bluetooth LED strip, plus a status screen on the rack monitor
(the server's own DisplayPort), running as one ZimaOS app shown as **Command Center**.

- UI: http://command.home.lab (old address http://lights.home.lab, or http://192.168.4.61:8787)
- Rack screen page: http://command.home.lab/screen/ (what the monitor shows; open it anywhere to preview)
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
Design: `docs/superpowers/specs/2026-10-11-led-notify-design.md`; rack screen: `docs/superpowers/specs/2026-11-07-command-center-design.md`.

## Rack screen

Two containers, one app (`docker-compose.yml`):
- `led-notify` samples CPU, memory, temperatures, the three drives and per-app CPU/memory (host cgroups,
  mounted read-only at `/host/cgroup`) every 2 s and serves `/screen/`, `/api/screen` (power + theme),
  `/api/screen/data` and `/api/screen/stream` (SSE).
- `led-notify-display` (`display/`) runs `seatd` + `cage` + Chromium in kiosk mode on `/dev/dri`
  (needs `SYS_TTY_CONFIG`, `/dev/tty0`, `/dev/tty1`, `/run/udev`), polls `/api/screen` every 5 s and turns the
  monitor's output on/off with `wlr-randr`. It only starts the browser while a DRM connector reports
  `connected` (with no monitor, cage + Chromium spin a CPU core). `docker logs led-notify-display` says
  `waiting for a monitor…`, `kiosk started`, `screen on|off (…)`.

Schedule (from the Rules → Settings page): on outside quiet hours — light theme between local sunrise and
sunset (location under "Rack screen"), dark otherwise; off during quiet hours, except that any alert the
strip shows at night (rule quiet behavior `dim`, i.e. high/critical) wakes it for "Wake at night for (min)"
(default 5). Test it without noise: `curl -X POST …/api/events -d '{"name":"screen.test","priority":"high","pattern":"white_flash","duration":1,"sound":""}'`.
Stopping `led-notify-display` never affects the strip; the console text returns to the monitor while it is stopped.

Note: `./deploy.sh` always (re)creates the container with `docker compose -f /DATA/AppData/led-notify/src/docker-compose.yml up -d --force-recreate`,
so repo compose edits take effect. `casaos-cli app-management install` only registers the ZimaOS dashboard tile (it creates no container),
so it runs once when `/var/lib/casaos/apps/led-notify` is missing; afterwards the synced compose is copied over the registered one.
All SSH steps share one multiplexed connection.

Before syncing, `./deploy.sh` copies `/DATA/AppData/led-notify/data/settings.json` and `devices.json` to
`<name>.pre-deploy-<UTC timestamp>` next to them (newest 5 of each kept), because older code treats a
`settings.json` saved by newer code as invalid and falls back to defaults.
Rollback = deploy the old commit, then restore the matching `settings.json.pre-deploy-*`.
