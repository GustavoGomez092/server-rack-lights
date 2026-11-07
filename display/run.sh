#!/bin/bash
# Keeps a Chromium kiosk on the rack monitor and powers the monitor on/off as led-notify's /api/screen says.
# cage 0.2.0 has no output-power protocol, so "off" disables the output with wlr-randr (no signal → standby).
set -u
URL=${SCREEN_URL:-http://127.0.0.1:8787/screen/}
API=${SCREEN_API:-http://127.0.0.1:8787/api/screen}
POLL_S=${POLL_S:-5}
FALLBACK_S=60   # led-notify unreachable this long: follow the default quiet hours (23:00-07:00)
DRM_STATUS=${DRM_STATUS:-/sys/class/drm/card*-*/status}

log() { echo "$(date '+%F %T') $*"; }
randr() { WAYLAND_DISPLAY=wayland-0 wlr-randr "$@"; }
nap() { sleep "$1" & wait $!; }  # interruptible, so SIGTERM is handled at once
# cage + Chromium spin a CPU core when no monitor is connected, so the kiosk only runs while one is.
# shellcheck disable=SC2086  # DRM_STATUS is a glob
monitor() { grep -qx connected $DRM_STATUS 2>/dev/null; }
outputs() { randr 2>/dev/null | awk '/^[^ ]/{n=$1} /Enabled:/{print n ":" $2}'; }

CAGE=0; SEATD=0; want=""; applied=""; ever_ok=""; last_ok=0; note=""

# Bash as PID 1 ignores SIGTERM unless trapped; stop cage and seatd so the VT is handed back cleanly.
stop() {
  for p in "$CAGE" "$SEATD"; do [ "$p" != 0 ] && kill "$p" 2>/dev/null; done
  wait 2>/dev/null
  exit 0
}
trap stop TERM INT

# Log a waiting reason once, not every poll.
say_once() { [ "$note" = "$1" ] || log "$1"; note=$1; }

# Sets $want from led-notify; before it has ever answered, or after a long outage, use 23:00-07:00.
desired() {
  local body hour
  if body=$(curl -fsS -m 3 "$API" 2>/dev/null); then
    ever_ok=1; last_ok=$(date +%s)
    case "$body" in *'"power":"off"'*) want=off ;; *) want=on ;; esac
  elif [ -z "$ever_ok" ] || (( $(date +%s) - last_ok > FALLBACK_S )); then
    hour=$((10#$(date +%H)))
    if (( hour >= 23 || hour < 7 )); then want=off; else want=on; fi
  fi
}

# Applies $want to every output; returns 1 while cage has no outputs yet. A monitor plugged in later
# shows up in `outputs` and gets the current state.
apply() {
  local outs o name enabled
  outs=$(outputs)
  [ -n "$outs" ] || return 1
  [ "$want|$outs" = "$applied" ] && return 0
  for o in $outs; do
    name=${o%%:*}; enabled=${o##*:}
    if [ "$want" = on ] && [ "$enabled" != yes ]; then randr --output "$name" --on
    elif [ "$want" = off ] && [ "$enabled" = yes ]; then randr --output "$name" --off
    fi
  done
  applied="$want|$(outputs)"
  log "screen $want ($outs)"
}

start_cage() {
  rm -f "$XDG_RUNTIME_DIR"/wayland-0*
  # WAYLAND_DISPLAY must stay unset for cage itself, or wlroots nests into a (missing) compositor.
  cage -- chromium --kiosk --no-sandbox --ozone-platform=wayland --disable-gpu --no-first-run --noerrdialogs \
    --disable-infobars --disable-extensions --disable-background-networking --disable-component-update \
    --disable-dev-shm-usage --disable-features=Translate,MediaRouter --password-store=basic \
    --renderer-process-limit=1 --user-data-dir=/tmp/chromium "$URL" >/tmp/cage.log 2>&1 &
  CAGE=$!
  applied=""
  log "kiosk started (pid $CAGE)"
  # Apply the wanted power as soon as an output exists: a start at night must not stay lit.
  for _ in $(seq 1 30); do
    apply && break
    kill -0 "$CAGE" 2>/dev/null || break
    nap 0.5
  done
}

mkdir -p -m 700 "$XDG_RUNTIME_DIR"
seatd -g root -l error &
SEATD=$!
nap 1
while true; do
  if ! monitor; then
    if [ "$CAGE" != 0 ] && kill -0 "$CAGE" 2>/dev/null; then  # kill -0 0 would hit our own group
      log "monitor unplugged: stopping kiosk"
      kill "$CAGE"; wait "$CAGE" 2>/dev/null
    fi
    CAGE=0
    say_once "waiting for a monitor on the DisplayPort"
    nap "$POLL_S"; continue
  fi
  if [ "$CAGE" = 0 ] || ! kill -0 "$CAGE" 2>/dev/null; then
    [ "$CAGE" = 0 ] || log "kiosk exited: $(tail -n 3 /tmp/cage.log | tr '\n' ' ')"
    CAGE=0
    # Never put Chromium's own (light) error page on the monitor: wait until the status page answers.
    if ! curl -fsS -m 3 -o /dev/null "$URL" 2>/dev/null; then
      say_once "waiting for the status page at $URL"
      nap "$POLL_S"; continue
    fi
    note=""
    desired
    start_cage
    continue
  fi
  nap "$POLL_S"
  desired
  [ -n "$want" ] && apply
done
