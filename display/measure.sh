#!/bin/bash
# What the rack screen costs in RAM, run on the server:  ssh zima 'bash -s' < display/measure.sh [snapshot|cost]
#   snapshot  one line: container_mib gtt_mib vram_mib avail_mib
#   cost      stop the screen, sample, start it, wait SETTLE_S (default 600), sample, print the difference.
# GPU buffers live in system RAM on this APU but are not charged to the container, so they are added in.
set -euo pipefail
NAME=led-notify-display
GPU=/sys/class/drm/card0/device
SETTLE_S=${SETTLE_S:-600}

mib() { echo $(( $1 / 1048576 )); }

container_mib() {
  local id cg
  id=$(sudo docker inspect -f '{{if .State.Running}}{{.Id}}{{end}}' "$NAME" 2>/dev/null || true)
  [ -n "$id" ] || { echo 0; return; }
  cg=/sys/fs/cgroup/system.slice/docker-$id.scope
  # What `docker stats` shows: memory.current minus inactive file cache.
  mib $(( $(cat "$cg/memory.current") - $(awk '/^inactive_file /{print $2}' "$cg/memory.stat") ))
}

snapshot() {
  echo "container_mib=$(container_mib) gtt_mib=$(mib "$(cat $GPU/mem_info_gtt_used)")" \
       "vram_mib=$(mib "$(cat $GPU/mem_info_vram_used)")" \
       "avail_mib=$(( $(awk '/^MemAvailable:/{print $2}' /proc/meminfo) / 1024 ))"
}

field() { sed -E "s/.*$1=([0-9]+).*/\1/" <<<"$2"; }

cost() {
  sudo docker stop "$NAME" >/dev/null
  sleep 20
  local a b
  a=$(snapshot)
  sudo docker start "$NAME" >/dev/null
  sleep "$SETTLE_S"
  b=$(snapshot)
  echo "stopped: $a"
  echo "running: $b"
  echo "cost_mib=$(( $(field container_mib "$b") + $(field gtt_mib "$b") - $(field gtt_mib "$a") \
                     + $(field vram_mib "$b") - $(field vram_mib "$a") ))" \
       "avail_drop_mib=$(( $(field avail_mib "$a") - $(field avail_mib "$b") ))"
}

"${1:-snapshot}"
