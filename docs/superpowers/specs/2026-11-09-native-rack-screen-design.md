# Native rack screen (GPUI) — design

Date: 2026-11-09. Status: draft for owner review.

## Intent

The rack monitor (Command Center's status screen, see `2026-11-07-command-center-design.md`) is drawn today by
a full Chromium kiosk running inside the `led-notify-display` container: seatd + cage (Wayland kiosk compositor)
+ Chromium showing `http://127.0.0.1:8787/screen/`. That container uses **~228 MiB** (`docker stats`,
2026-11-09; cgroup: 243 MiB current, 151 MiB anon) to show one display-only page that changes every 2 s.

RAM is the server's limiting resource (14.6 GiB, swap was full on 2026-11-08; a 32 GB upgrade is coming but
the waste is the point). The owner wants the browser gone: the screen drawn by a small **native program** built
with **GPUI** (https://gpui.rs, Zed's Rust UI framework), looking **the same as today**.

The why is memory. Everything else stays as it is.

## Goals

1. A native Rust program using GPUI (or the lighter fallback, see Decided) replaces Chromium inside the `led-notify-display` container. seatd and cage
   stay; the program runs as cage's single Wayland client, full screen, no cursor.
2. **Same look as today.** The reference is the current page:
   - Screenshots at the monitor's real size (800×480): `assets/2026-11-09-rack-screen-ref-dark.png`,
     `…-ref-light.png`, `…-ref-banner.png` (two alerts showing).
   - The exact values (colors, proportions, font sizes, radii, thresholds) are in the current
     `led_notify/web/static/screen/screen.css` and `screen.js` — read them from git history once they are
     deleted (last commit containing them: the one before this work's deletion). Sizes there are in `vw`/`vh`;
     at 800×480, 1vw = 8 px and 1vh = 4.8 px. The layout must still hold at any 16:9-ish size (sizes scale with
     the window), but 800×480 is the one that must match.
   - Layout: header row (host name left; `up N days · 5:12 PM` right, muted), alert banners full width under it,
     then a 1.3fr / 1fr grid: three ring gauges (CPU, RAM, CPU temp) top-left, Disks panel bottom-left, Top apps
     panel on the right spanning both rows.
   - Font: Inter (as today, `fonts-inter`), tabular numbers.
3. **Same behavior as today**, all driven by the existing `GET /api/screen/stream` (SSE, event `screen`, a push
   every 2 s; payload sample in `assets/2026-11-09-rack-screen-sample-data.json`):
   - Theme `light`/`dark` from `screen.theme`; brightness from `screen.brightness` (10–100 %): every color is
     mixed toward black by that percentage (`color-mix(in srgb, C keep%, #000)` = each sRGB channel × keep).
   - `screen.power == "off"` → the whole screen is pure black (#000), nothing else drawn. The output itself is
     never turned off (see Constraints).
   - Gauges: CPU % (0–100), RAM % with `used / total GB` detail, CPU temp with the ring spanning **30–100 °C**
     and `· drive N°` detail. Missing value → `–` and an empty ring. Ring/bar color turns warn/bad at the same
     limits: CPU 80/95 %, RAM 85/95 %, CPU temp 75/85 °C, disks 85/95 %.
   - Disks: one row per disk (label, `used / total GB`, a bar); unmounted → `not connected` in italics, empty bar.
   - Top apps: up to 5 rows (name ellipsized, CPU % with one decimal under 10 else whole, memory GB);
     none → `No app data yet`.
   - Banners: each item `text` + its `since` time; red style for priority `critical`/`high`, amber otherwise;
     hidden when empty.
   - GB format: one decimal under 100, whole number from 100 up (bytes / 1024³). Uptime: `up N day(s)` ≥ 1 day,
     else `up N h`, else `up N min` (min 1). Times are the **server's wall time read straight from the ISO
     string** (`T(HH):(MM)` → `5:12 PM`), never converted through the program's own timezone.
   - Stale: no push for **10 s** → keep the last numbers, gauges and panels at 45 % opacity, and
     ` · reconnecting` in warn color after the meta text. Reconnect automatically forever (led-notify restarts,
     container start order). Before the first push, show the empty layout (`–`), not an error.
   - Motion: ring arcs and disk bars glide to new values (~0.3 s ease-out) and colors fade on theme/brightness
     change (~0.3 s), as today; the very first frame appears without a fade. (My call: today's page has this;
     dropping it is acceptable if it costs real memory or CPU — say so in the report.)
4. **Memory: the screen must cost ≤ 150 MiB**, measured on the server after it has run ≥ 1 h with live data,
   and must not grow over a day. "Cost" = the container's `docker stats` memory **plus** the change in the GPU's
   system-memory use (`/sys/class/drm/card0/device/mem_info_gtt_used`, and `mem_info_vram_used` out of the
   BIOS-reserved carve-out) between "screen container stopped" and "running", so GPU allocations the container
   isn't charged for still count (my call). Also report the drop in host `MemAvailable`. Lower is the point; report the real
   number next to today's 228 MiB. CPU at idle must not be worse than today (~1–2 % of one core in
   `docker stats`).
5. The web version of the screen goes away (owner's decision): delete `led_notify/web/static/screen/`, the
   "rack screen" pill link in `led_notify/web/static/index.html`, and update the README lines that point to
   `/screen/` (`README.md` lines 7 and ~40). Tests that load `/screen/` (`tests/test_screen_api.py::
   test_screen_page_served`) are removed or replaced. `/api/screen`, `/api/screen/data` and
   `/api/screen/stream` stay — the native program uses the stream.

## Non-goals

- No change to led-notify's Python side beyond the deletions in Goal 5: schedule, preview, brightness sliders,
  banners, stats sampling and the API are untouched.
- No new screen content, no redesign, no interactivity (no touch, keyboard or mouse).
- No change to how the LED strip works; the display container stays separate so a screen crash never touches it.

## Anti-goals (would make this a failure even if it "works")

- The monitor goes to sleep. It is an HCT board (DP-2 via DP→HDMI adapter, 800×480@60.584, no DDC/CI) that
  falls asleep when the signal is lost and **only wakes on a USB power cycle**. Nothing may disable the output or
  stop presenting frames long enough for the signal to drop — including during restarts of the program inside
  cage. Today's `display/run.sh` only ever turns outputs **on**; keep that. What is known: an explicit
  `wlr-randr --off` put it to sleep; container recreates by `./deploy.sh` (cage exits, the kernel console
  takes the output back for a moment) have happened since without it sleeping. Still, keep cage running and
  restart only the program inside it when the program dies (my call: cheapest safeguard). If the monitor does
  fall asleep during development, stop and ask the owner to unplug/replug its USB power; never loop on it.
- Memory not meaningfully lower than today, or growing over time (GPU/driver allocations included).
- A core spinning: today cage + Chromium spin a core when no monitor is connected, which is why `run.sh` only
  starts the kiosk while `/sys/class/drm/card*-*/status` says `connected`. Keep that gating; the native program
  must also idle between 2 s pushes and animations (no continuous redraw loop).
- A visibly different screen (wrong colors, sizes, spacing, font) at 800×480 next to the reference shots.
- Building on the server in a way that pushes it into swap while it serves everything else.

## Constraints

- Server: Lenovo M715q, Ryzen 5 2400GE, **Radeon Vega (Raven Ridge, amdgpu)**, ZimaOS (read-only rootfs,
  everything in Docker). `/dev/dri` (card0, renderD128) is already passed into the container; tty0/tty1 too,
  `/run/udev` read-only, `cap_add: SYS_TTY_CONFIG`, host network.
- GPUI on crates.io: `gpui` 0.2.2 (2026-10-30). On Linux it renders with **Vulkan via blade-graphics** and talks
  Wayland or X11. So the image needs Mesa's Vulkan driver for AMD (RADV) and the Wayland/xkbcommon/fontconfig
  runtime libs. Component library `gpui-component` 0.9.1 exists (longbridge); using it is allowed but not
  required — memory wins over convenience.
- cage today runs `WLR_RENDERER=pixman` (software). A Vulkan client hands cage GPU buffers (linux-dmabuf), which
  a pixman renderer may not accept; expect to switch cage to `gles2` (or `vulkan`). Verify on the real server.
- The image is built from `display/` by `./deploy.sh` (`docker build -t led-notify-display:local display` on the
  server). The current image is 836 MB; a smaller one is welcome but not a goal.
- Compose (`docker-compose.yml`, service `display`): `mem_limit: 1g`, `cpus: 1.0`. Lower `mem_limit` to fit the
  new program with headroom once its real usage is known.
- `tests/test_display_run.py` tests `run.sh` with fake seatd/cage/wlr-randr/curl; keep those behaviors tested
  (never `--off`, re-enable a disabled output, wait for led-notify before starting, quick SIGTERM).
- Build machine: the server (x86_64), after the RAM upgrade (see Decided). The owner's Mac is arm64 with no
  Docker daemon and is not used for building.
- Repo conventions: TDD, small focused files, comments only where the why isn't obvious, merges and pushes only
  with the owner's explicit OK. Never print secrets. SSH to the server only through the multiplexed connection.

## Decided

- **GPUI** as the framework (owner).
- **Same look as today** (owner).
- **Web version of the screen removed** (owner).
- Memory is the goal; Rust/UI and Dioxus were dropped because Dioxus desktop is itself a webview (owner chose
  memory; my research).
- The program reads the existing SSE stream; led-notify's Python side doesn't change (my call).
- Target ≤ 150 MiB (my number, told to the owner; not objected to).

## Left to the builder

- Crate layout (e.g. a Cargo crate under `display/`), SSE/HTTP client crate, how state flows into GPUI views.
- Drawing the rings (GPUI path/canvas vs. a component), how motion is done.
- How `run.sh` changes (launch the program instead of Chromium; how it waits for led-notify; keep gating).
- How to prove the look: e.g. a headless or nested-compositor screenshot at 800×480 fed the sample payload,
  compared side by side with the reference PNGs, plus a photo/screenshot from the real monitor.
- Unit tests for the formatting and threshold rules in Goal 3 (Rust tests).
- Rollback: keep the previous `led-notify-display` image tagged so the Chromium version can come back with one
  command until the native one has run a few days.

- **Build on the server, inside the normal `./deploy.sh` Docker build, after the 32 GB RAM upgrade** (owner,
  2026-11-09). Work starts once `free -h` shows ~30 GiB. Use a multi-stage Dockerfile so the runtime image
  carries only the program and its runtime libs; keep compiled dependencies cached between deploys.
- **Memory beats GPUI** (owner, 2026-11-09): start with a go/no-go spike — a minimal GPUI window under cage on
  the real server showing static text and one ring, measured as in Goal 4. If it can't stay ≤ 150 MiB, drop
  GPUI and build the same screen with a lighter native approach (e.g. a plain Wayland client drawing with a
  small software rasterizer into shared memory, which also lets cage stay on `pixman`), and tell the owner the
  measured GPUI number. Goals, look and behavior don't change with the approach.
