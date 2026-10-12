#!/usr/bin/env bash
# Sync source to the ZimaOS server, build the image there, and (re)start the app.
set -euo pipefail
HOST="${HOST:-zima}"
APP=/DATA/AppData/led-notify
REGISTERED=/var/lib/casaos/apps/led-notify
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=/tmp/led-notify-ssh-%r@%h:%p" -o ControlPersist=60)
cd "$(dirname "$0")"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)

# One multiplexed SSH connection is reused for every step (the server bans IPs after aborted connections).
# Before anything changes, back up the live data files (newest 5 kept each): an older image treats a
# settings.json written by newer code as invalid, so a rollback needs the matching backup.
ssh "${SSH_OPTS[@]}" "$HOST" "set -e
sudo mkdir -p $APP/src $APP/data && sudo chown -R \$(id -un) $APP/src
for f in settings.json devices.json; do
  if sudo test -f $APP/data/\$f; then
    sudo cp -p $APP/data/\$f $APP/data/\$f.pre-deploy-$STAMP
    sudo find $APP/data -maxdepth 1 -name \"\$f.pre-deploy-*\" | sort -r | tail -n +6 | while read -r old; do sudo rm -f \"\$old\"; done
  fi
done"
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
out=\$(curl -fsS --retry 15 --retry-connrefused --retry-delay 2 http://127.0.0.1:8787/api/state)
printf '%s\n' \"\$out\" | head -c 600
echo"
