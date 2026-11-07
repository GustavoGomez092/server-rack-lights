# Command Center — design

Date: 2026-11-07. Status: draft for owner review.

## Intent

The ZimaOS server (Lenovo M715q, Ryzen 5 2400GE with Vega iGPU, hostname GomezPalacios) has a 16:9 monitor
sitting on top of the rack, plugged into the box's own DisplayPort. Today that port shows the Linux text
console. The owner wants it to show a glanceable server status screen instead, driven by the server itself —
no second computer, no Raspberry Pi.

led-notify (this repo) already reads most of what the screen needs (CPU temperature, disks, Docker events) and
already owns the house's "quiet hours" settings. So the screen becomes part of led-notify, and led-notify is
presented to the owner as one app: **Command Center** — LED strip alerts + rack status screen.

Read from arm's length, glanced at in passing. It should feel like a calm, good-looking control-room panel.

## Goals

1. The rack monitor shows a full-screen status page (no browser chrome, no cursor, no console text) whenever
   the screen is on.
2. The page shows, live (refresh about every 2 s):
   - **CPU** usage % (whole machine).
   - **RAM** used % plus `used / total GB` (e.g. `10.0 / 14.6 GB`), "used" as `free -m` reports it.
   - **Temperature**: CPU temperature as the main gauge (hwmon `k10temp`, same sensor the thermal monitor uses);
     main NVMe drive temperature as smaller secondary text.
   - **Disk space** for the three real drives, each a bar with `used / total`:
     Main (`/DATA`, NVMe), Movies SSD (`/DATA/Media/MoviesSSD`), Backup (`/DATA/Backup`, USB SSD).
     A drive that is not mounted shows as "not connected", not as an error or 0%.
   - **Top 5 apps** by CPU, each with its CPU % and memory. Shown as friendly app names (Paperclip, Jellyfin,
     qBittorrent, Hermes, AdGuard…), **never** raw process names or PIDs.
   - Header: hostname, uptime, clock.
3. Look: **layout A "Control room"** from the brainstorm mockup (`docs/superpowers/specs/assets/2026-11-07-command-center-look.html`, option A; only its dark version exists — the light version is the builder's design): three ring gauges
   (CPU, RAM, CPU temp) top-left, disk bars bottom-left, top-apps list in a panel on the right, header row across
   the top. **Light theme during the day, dark theme at night.**
4. Screen schedule, all driven by led-notify's existing `quiet_hours` settings (`enabled`, `start`, `end`,
   currently 23:00–07:00):
   - Outside quiet hours: screen on. Light theme from sunrise to sunset, dark theme from sunset to quiet-hours
     start.
   - At quiet-hours start: the monitor turns **fully off** (DPMS/standby — the panel goes dark, not a black page).
   - During quiet hours: an event that the strip would show at night — i.e. a rule whose quiet behavior is
     `dim` (by default high and critical priority) — wakes the screen for **5 minutes**, in dark theme, then it
     turns off again. Events the strip suppresses at night (low/normal) do **not** wake it. A new qualifying
     event while awake restarts the 5 minutes.
   - At quiet-hours end: screen on for the day.
   - If quiet hours are disabled in settings, the screen never turns off.
   - Changing quiet hours in the settings page takes effect without a restart.
5. Whenever an alert is active or an event just fired (day or night), a banner across the top of the status page
   says what it is in plain words and when (e.g. "Internet down · 2:14 AM"). When woken at night, this is the
   first thing visible.
6. Renamed to **Command Center** everywhere the owner looks: ZimaOS dashboard tile title/tagline, the web UI's
   title/header, the status page, and a new address `http://command.home.lab`. `http://lights.home.lab` keeps
   working (same app). The settings UI stays where it is functionally — Command Center's settings page is
   led-notify's existing UI.

## Non-goals

- No touch or keyboard interaction on the rack monitor; it's display-only.
- No history graphs/sparklines (that was option B; owner picked A).
- No new alerting logic; the strip's rules, priorities and quiet behavior are unchanged.
- Not renaming the container (`led-notify`), the repo folder, the Python package, or the GitHub repo
  (`server-rack-lights`). Owner agreed: renaming those risks deploys/backups for no visible gain.
- No second computer, no Pi, no casting from another device.

## Anti-goals (failure even if it "works")

- **The dashboard must never put the server at risk.** This box is the whole house's DNS (AdGuard) and runs the
  media stack, Paperclip and Hermes. The status screen must use little CPU/RAM (target: the display part well
  under 5% of one core and a few hundred MB RAM while idle-refreshing), must not hammer the Docker API, and must
  not need host changes that survive poorly across ZimaOS updates (rootfs is read-only squashfs; `/etc` and
  `/DATA` persist).
- **A browser hang or crash must not take down LED alerts or chimes.** The display renderer runs in its own
  container; led-notify must keep working whether or not the display container is running, and whether or not
  a monitor is plugged in.
- **Never lock the owner out of the machine.** If the display stack fails, the server must still be reachable
  over SSH and the web UI as today; a local console being unavailable is acceptable.
- Never show raw PIDs / process command lines on the screen.
- Never a white flash at night: the screen must not light up in light theme during quiet hours.

## Constraints and facts the builder needs

- Host: ZimaOS 1.7.1, kernel 6.18.9. `amdgpu` loaded; `/dev/dri/card0` (root:video) and `/dev/dri/renderD128`
  exist; connectors `card0-DP-1`…`DP-4` (all "disconnected" on 2026-11-07 — no monitor plugged in yet).
  Kernel cmdline has `console=tty1 quiet splash`; the text console currently owns the screen.
- Rootfs is read-only; `/etc` is a persistent overlay; systemd units live in `/etc/systemd/system`.
  Host has no `openssl`, `jq` without regex, `ping` broken, Docker Compose 2.32, passwordless sudo.
- led-notify today: one Python container (`network_mode: host`, port 8787, Docker socket read-only,
  `/DATA` mounted read-only at `/host/DATA`, `/dev/nvme0`, `/dev/snd`, D-Bus for BLE). Existing pieces to reuse:
  `monitors/thermal.py` (`read_hwmon_temp`), `monitors/disk.py` (`disk_usage_pct`), `engine/quiet.py`
  (`in_window`), `engine/arbiter.py` (`_quiet`, rule `quiet_behavior`), `hub.py` (SSE pub/sub),
  `store/settings.py` (validated settings with defaults; older code falls back to defaults on unknown keys —
  see the deploy note in README).
- ZimaOS shows **one dashboard tile per compose app**. Command Center must stay a single tile: the display
  container goes in the same compose (`name: led-notify`), registered app stays `led-notify`.
- Dashboard tile metadata comes from ZimaOS's stored compose; follow the README / server skill procedure
  (copy compose over `/var/lib/casaos/apps/led-notify/docker-compose.yml`, restart `zimaos-app-management`).
- `command.home.lab`: add to the proxy repo (`HomeLab/proxy`: Caddyfile site + `tools/directory.py` label;
  `./deploy.sh` there). The `*.home.lab` AdGuard wildcard already resolves it.
- Timezone `America/Los_Angeles`. Sunrise/sunset must be computed locally (no internet dependency).
- Existing tests: pytest, ~325+; keep them green; new code gets tests (TDD).

## Decided by the owner

- Use the server's own DisplayPort; no extra device.
- Content: disk space, RAM, CPU, temperature, top 5 **apps** (not processes/PIDs).
- Layout A "Control room"; light by day, dark by night.
- 23:00 (quiet-hours start) screen fully off; wakes 5 min only for the same high/critical events the strip shows
  at night; on again at quiet-hours end (07:00). All from led-notify's quiet-hours settings.
- Grow led-notify into "Command Center" rather than a separate app.
- Rename only what the owner sees; `lights.home.lab` keeps working.

## Calls made by Claude (owner may override)

- **Two containers, one app.** led-notify grows the stats API, the status page and the screen on/off decision;
  a separate slim display container (e.g. a Wayland kiosk compositor such as `cage` + Chromium/WPE in kiosk
  mode, pointed at `http://127.0.0.1:8787/screen`) only renders it. Reason: anti-goal on browser crashes.
- **Day/night switch at local sunrise/sunset**, computed for a configured location (default Los Angeles area),
  with a setting to change the location. Owner did not object to this when played back.
- **Top 5 = one row per container**, sorted by CPU, with a friendly name (ZimaOS app title where a container is
  its app's main container, otherwise a small built-in name map, otherwise the container name prettified).
  The servarr stack's containers appear individually (qBittorrent, Sonarr…) rather than as one "servarr" row.
- **Event banner** on the status page for active alerts/recent events, day and night.
- **Screen wake duration 5 min** is a setting (default 5) next to quiet hours.
- Page refresh ~2 s, pushed over the existing SSE hub or a dedicated SSE stream — builder's choice.

## Left to the builder

- How the display container gets DRM master and input-free kiosk mode on ZimaOS (seat handling, which VT,
  stopping/coexisting with `getty@tty1`, `/dev/dri` + `/dev/tty*` access, cursor hiding). **De-risk this first**
  with a throwaway spike before building the rest; if it can't be made reliable, stop and report.
- How led-notify tells the display to power the monitor off/on (e.g. a small control endpoint the kiosk side
  polls, `wlopm`/`wlr-randr` DPMS, or DRM connector DPMS) — as long as "off" means the panel really sleeps.
- How CPU/memory per container is gathered cheaply (cgroup v2 files vs Docker stats API) within the anti-goal.
- Hot-plug behavior (monitor connected after boot, unplugged and replugged) — it should recover by itself.
- Settings UI additions (location, wake minutes, maybe a "test screen wake" button) and their exact placement.
- Visual polish of layout A within the mockup's structure, both themes.
- Updating the README, the server skill (`operating-zima-home-server`) and the proxy directory label.

## Verified on the server (spike, 2026-11-07)

A throwaway `debian:trixie-slim` image with `cage` 0.2.0 + `chromium` 156 + `seatd` + `wlr-randr`:
- Headless backend renders the led-notify page; `wlr-randr --output X --off` / `--on` works under cage
  (cage 0.2.0 has no wlr-output-power-management, so `wlopm` does NOT work — use `wlr-randr`).
- Real DRM backend works with: `--device /dev/dri --device /dev/tty0 --device /dev/tty1`,
  `--cap-add SYS_TTY_CONFIG`, `-v /run/udev:/run/udev:ro`, `seatd -g root` inside the container with
  `LIBSEAT_BACKEND=seatd`, `WLR_BACKENDS=drm`, `WLR_RENDERER=pixman`, `WLR_LIBINPUT_NO_DEVICES=1`
  (no input devices mounted → no cursor). `WAYLAND_DISPLAY` must NOT be set for cage itself (wlroots then
  picks the nested wayland backend). Cage keeps running with no monitor connected.
- Without `SYS_TTY_CONFIG` seatd cannot switch the VT to graphics mode.
- Not yet verified: picture on a real panel, hot-plug, what this monitor does on DPMS off. Needs the monitor
  plugged in; the owner checks it once at the end.

## Success looks like

- Plug the monitor into the rack box: within a minute it shows the Command Center status page, full-screen,
  light theme in daytime, readable from arm's length.
- At 23:00 the monitor goes dark (standby). A test critical event at night lights it up in dark theme with the
  banner, and it goes dark again 5 minutes later. A known phone coming home at night (`device.known_arrived`, low) does not wake it; an unknown device joining late (`device.new_late`, high) does — same as the strip.
- At 07:00 it comes back on by itself.
- Killing the display container leaves the LED strip, chimes and web UI working; it restarts by itself.
- After a server reboot everything comes back without manual steps.
- ZimaOS dashboard shows one tile titled "Command Center"; `command.home.lab` and `lights.home.lab` both open it.
