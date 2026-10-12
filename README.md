# led-notify

Homelab notifications on a BanlanX SP548E Bluetooth LED strip, running as a ZimaOS app.

- UI: http://192.168.0.61:8787
- Webhook: `curl -X POST http://192.168.0.61:8787/api/events -H 'Content-Type: application/json' -d '{"name":"backup.done","pattern":"green_flash"}'`
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
.venv/bin/python -m led_notify.smoke --url http://192.168.0.61:8787   # watch every pattern
```

The strip accepts one Bluetooth connection: while the service runs, the BanlanX phone app can't connect.
Design: `docs/superpowers/specs/2026-10-11-led-notify-design.md`.

Note: `./deploy.sh` always (re)creates the container with `docker compose -f /DATA/AppData/led-notify/src/docker-compose.yml up -d --force-recreate`,
so repo compose edits take effect. `casaos-cli app-management install` only registers the ZimaOS dashboard tile (it creates no container),
so it runs once when `/var/lib/casaos/apps/led-notify` is missing; afterwards the synced compose is copied over the registered one.
All SSH steps share one multiplexed connection.
