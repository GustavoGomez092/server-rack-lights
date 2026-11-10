#!/bin/bash
# cage's only client: keeps the screen program running. cage exits when its client does, and a cage restart
# could drop the monitor's signal (it only wakes from that with a power cycle), so only the program restarts.
BIN=${SCREEN_BIN:-/usr/local/bin/rack-screen}
while true; do
  "$BIN"
  echo "$(date '+%F %T') rack-screen exited ($?), restarting" >&2
  sleep 1
done
