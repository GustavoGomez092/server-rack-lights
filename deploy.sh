#!/usr/bin/env bash
# Sync source to the ZimaOS server, build the image there, and (re)start the app.
set -euo pipefail
HOST="${HOST:-zima}"
APP=/DATA/AppData/led-notify
COMPOSE_INSTALLED=/var/lib/casaos/apps/led-notify/docker-compose.yml
cd "$(dirname "$0")"

ssh "$HOST" "sudo mkdir -p $APP/src $APP/data && sudo chown -R \$(id -un) $APP/src"
rsync -az --delete --exclude .venv --exclude .git --exclude __pycache__ --exclude .pytest_cache \
  --exclude data ./ "$HOST:$APP/src/"
ssh "$HOST" "cd $APP/src && sudo docker build -t led-notify:local ."

if ssh "$HOST" "sudo test -f $COMPOSE_INSTALLED"; then
  ssh "$HOST" "sudo docker compose -f $COMPOSE_INSTALLED up -d --force-recreate"
else
  ssh "$HOST" "sudo casaos-cli app-management install -f $APP/src/docker-compose.yml"
fi

ssh "$HOST" "curl -fsS --retry 15 --retry-connrefused --retry-delay 2 http://127.0.0.1:8787/api/state" | head -c 600
echo
