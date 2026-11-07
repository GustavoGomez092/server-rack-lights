#!/bin/bash
# Keeps a Chromium kiosk on the rack monitor and powers the monitor on/off as led-notify's /api/screen says.
# cage 0.2.0 has no output-power protocol, so "off" disables the output with wlr-randr (no signal → standby).
set -u
URL=${SCREEN_URL:-http://127.0.0.1:8787/screen/}
API=${SCREEN_API:-http://127.0.0.1:8787/api/screen}
POLL_S=5
FALLBACK_S=60   # led-notify unreachable this long: follow the default quiet hours (23:00-07:00)

log() { echo "$(date '+%F %T') $*"; }
# cage + Chromium spin a CPU core when no monitor is connected, so the kiosk only runs while one is.
monitor() { grep -qx connected /sys/class/drm/card*-*/status 2>/dev/null; }
randr() { WAYLAND_DISPLAY=wayland-0 wlr-randr "$@"; }

mkdir -p -m 700 "$XDG_RUNTIME_DIR"
seatd -g root -l error &

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
}

want=""; applied=""; last_ok=$(date +%s); CAGE=0; waiting=""
sleep 1
while true; do
  if ! monitor; then
    if kill -0 "$CAGE" 2>/dev/null; then
      log "monitor unplugged: stopping kiosk"
      kill "$CAGE"; wait "$CAGE" 2>/dev/null
    fi
    [ -n "$waiting" ] || log "waiting for a monitor on the DisplayPort"
    waiting=1; sleep "$POLL_S"; continue
  fi
  waiting=""
  if ! kill -0 "$CAGE" 2>/dev/null; then
    [ "$CAGE" = 0 ] || log "kiosk exited: $(tail -n 3 /tmp/cage.log | tr '\n' ' ')"
    start_cage
    sleep "$POLL_S"
    continue
  fi
  sleep "$POLL_S"
  if body=$(curl -fsS -m 3 "$API" 2>/dev/null); then
    last_ok=$(date +%s)
    case "$body" in *'"power":"off"'*) want=off ;; *) want=on ;; esac
  elif (( $(date +%s) - last_ok > FALLBACK_S )); then
    hour=$((10#$(date +%H)))
    if (( hour >= 23 || hour < 7 )); then want=off; else want=on; fi
  fi
  [ -n "$want" ] || continue
  # Outputs as "name:yes|no"; a monitor plugged in later shows up here and gets the current state.
  outs=$(randr 2>/dev/null | awk '/^[^ ]/{n=$1} /Enabled:/{print n ":" $2}')
  [ -n "$outs" ] || continue
  state="$want|$outs"
  [ "$state" = "$applied" ] && continue
  for o in $outs; do
    name=${o%%:*}; enabled=${o##*:}
    if [ "$want" = on ] && [ "$enabled" != yes ]; then randr --output "$name" --on
    elif [ "$want" = off ] && [ "$enabled" = yes ]; then randr --output "$name" --off
    fi
  done
  applied="$want|$(randr 2>/dev/null | awk '/^[^ ]/{n=$1} /Enabled:/{print n ":" $2}')"
  log "screen $want ($outs)"
done
