#!/bin/bash
# Keeps the native rack screen (rack-screen, via screen-loop.sh) on the rack monitor and its output enabled.
# The output is never turned off: the rack monitor (HCT board) falls asleep without a signal and only a power
# cycle wakes it again. Night "off" is a black page that the status page draws itself (/api/screen power).
set -u
URL=${SCREEN_URL:-http://127.0.0.1:8787/api/screen/data}
POLL_S=${POLL_S:-5}
DRM_STATUS=${DRM_STATUS:-/sys/class/drm/card*-*/status}

log() { echo "$(date '+%F %T') $*"; }
randr() { WAYLAND_DISPLAY=wayland-0 wlr-randr "$@"; }
nap() { sleep "$1" & wait $!; }  # interruptible, so SIGTERM is handled at once
# cage spins a CPU core when no monitor is connected, so the kiosk only runs while one is.
# shellcheck disable=SC2086  # DRM_STATUS is a glob
monitor() { grep -qx connected $DRM_STATUS 2>/dev/null; }
outputs() { randr 2>/dev/null | awk '/^[^ ]/{n=$1} /Enabled:/{print n ":" $2}'; }

CAGE=0; SEATD=0; note=""

# cage (0.2) waits for its client on SIGTERM and never kills it, and its client screen-loop.sh never exits on
# its own; so cage runs in its own process group and the whole group (cage, loop, rack-screen) is stopped.
stop_cage() {
  [ "$CAGE" != 0 ] || return 0
  kill -TERM -- "-$CAGE" 2>/dev/null
  wait "$CAGE" 2>/dev/null
  CAGE=0
}

# Bash as PID 1 ignores SIGTERM unless trapped; stop cage and seatd so the VT is handed back cleanly.
stop() {
  stop_cage
  [ "$SEATD" != 0 ] && kill "$SEATD" 2>/dev/null
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
  set -m  # job control: cage gets its own process group (see stop_cage)
  cage -- "${LOOP:-$(dirname "$0")/screen-loop.sh}" >/tmp/cage.log 2>&1 &
  CAGE=$!
  set +m
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
    fi
    stop_cage
    say_once "waiting for a monitor on the DisplayPort"
    nap "$POLL_S"; continue
  fi
  if [ "$CAGE" = 0 ] || ! kill -0 "$CAGE" 2>/dev/null; then
    [ "$CAGE" = 0 ] || log "kiosk exited: $(tail -n 3 /tmp/cage.log | tr '\n' ' ')"
    stop_cage  # a crashed cage leaves its loop behind; never two rack-screens
    # Start once led-notify answers, so the first frame already has numbers and the right theme.
    if ! curl -fsS -m 3 -o /dev/null "$URL" 2>/dev/null; then
      say_once "waiting for led-notify at $URL"
      nap "$POLL_S"; continue
    fi
    note=""
    start_cage
    continue
  fi
  nap "$POLL_S"
  apply
done
