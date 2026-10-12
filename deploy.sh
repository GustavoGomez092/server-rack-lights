#!/usr/bin/env bash
# Sync source to the ZimaOS server, build the image there, and (re)start the app.
set -euo pipefail
HOST="${HOST:-zima}"
APP=/DATA/AppData/led-notify
REGISTERED=/var/lib/casaos/apps/led-notify
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=/tmp/led-notify-ssh-%r@%h:%p" -o ControlPersist=60)
cd "$(dirname "$0")"

# One multiplexed SSH connection is reused for every step (the server bans IPs after aborted connections).
ssh "${SSH_OPTS[@]}" "$HOST" "sudo mkdir -p $APP/src $APP/data && sudo chown -R \$(id -un) $APP/src"
rsync -az --delete --exclude .venv --exclude .git --exclude __pycache__ --exclude .pytest_cache \
  --exclude /data -e "ssh ${SSH_OPTS[*]}" ./ "$HOST:$APP/src/"

# Build, register (first time only), recreate from the synced compose, then health-check.
ssh "${SSH_OPTS[@]}" "$HOST" "set -e
cd $APP/src && sudo docker build -t led-notify:local .
if sudo test -d $REGISTERED; then
  sudo cp $APP/src/docker-compose.yml $REGISTERED/docker-compose.yml
else
  sudo casaos-cli app-management install -f $APP/src/docker-compose.yml || true
fi
sudo docker compose -f $APP/src/docker-compose.yml up -d --force-recreate
curl -fsS --retry 15 --retry-connrefused --retry-delay 2 http://127.0.0.1:8787/api/state | head -c 600
echo"
