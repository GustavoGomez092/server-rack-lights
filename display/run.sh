#!/bin/bash
# Keeps a Chromium kiosk on the rack monitor and its output enabled.
# The output is never turned off: the rack monitor (HCT board) falls asleep without a signal and only a power
# cycle wakes it again. Night "off" is a black page that the status page draws itself (/api/screen power).
set -u
URL=${SCREEN_URL:-http://127.0.0.1:8787/screen/}
POLL_S=${POLL_S:-5}
DRM_STATUS=${DRM_STATUS:-/sys/class/drm/card*-*/status}

log() { echo "$(date '+%F %T') $*"; }
randr() { WAYLAND_DISPLAY=wayland-0 wlr-randr "$@"; }
nap() { sleep "$1" & wait $!; }  # interruptible, so SIGTERM is handled at once
# cage + Chromium spin a CPU core when no monitor is connected, so the kiosk only runs while one is.
# shellcheck disable=SC2086  # DRM_STATUS is a glob
monitor() { grep -qx connected $DRM_STATUS 2>/dev/null; }
outputs() { randr 2>/dev/null | awk '/^[^ ]/{n=$1} /Enabled:/{print n ":" $2}'; }

CAGE=0; SEATD=0; note=""

# Bash as PID 1 ignores SIGTERM unless trapped; stop cage and seatd so the VT is handed back cleanly.
stop() {
  for p in "$CAGE" "$SEATD"; do [ "$p" != 0 ] && kill "$p" 2>/dev/null; done
  wait 2>/dev/null
  exit 0
}
trap stop TERM INT

# Log a waiting reason once, not every poll.
say_once() { [ "$note" = "$1" ] || log "$1"; note=$1; }

# Enables any disabled output (e.g. left off by an older version); returns 1 while cage has no outputs yet.
apply() {
  local outs o
  outs=$(outputs)
  [ -n "$outs" ] || return 1
  for o in $outs; do
    [ "${o##*:}" = yes ] && continue
    randr --output "${o%%:*}" --on
    log "output ${o%%:*} on"
  done
}

start_cage() {
  rm -f "$XDG_RUNTIME_DIR"/wayland-0*
  # WAYLAND_DISPLAY must stay unset for cage itself, or wlroots nests into a (missing) compositor.
  cage -- chromium --kiosk --no-sandbox --ozone-platform=wayland --disable-gpu --no-first-run --noerrdialogs \
    --disable-infobars --disable-extensions --disable-background-networking --disable-component-update \
    --disable-dev-shm-usage --disable-features=Translate,MediaRouter --password-store=basic \
    --renderer-process-limit=1 --user-data-dir=/tmp/chromium "$URL" >/tmp/cage.log 2>&1 &
  CAGE=$!
  log "kiosk started (pid $CAGE)"
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
    start_cage
    continue
  fi
  nap "$POLL_S"
  apply
done
