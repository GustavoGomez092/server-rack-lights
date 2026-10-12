# led-notify — Audio Chimes Design Spec

**Date:** 2026-10-12
**Status:** Draft for review
**Extends:** `2026-10-11-led-notify-design.md`

## 1. Purpose

Add a second sensory cue: short chimes through the server's built-in speaker, driven by the same
rules, priorities and flows as the light patterns, so an event isn't missed when nobody is looking
at the strip.

**Success criteria**
- An event whose light is shown also plays its rule's chime through the internal speaker.
- Each rule has a `sound` (a built-in, an uploaded file, or none), editable in the Rules tab.
- At night (quiet hours) only **critical** rules chime, at a lower night volume.
- Persistent alerts chime once when they start; recovery plays its own chime (via the follow-up event).
- Users can upload their own short WAV/MP3 sounds and pick them per rule.
- Audio problems never break or delay the lights.

## 2. Hardware (verified 2026-10-12)

Lenovo ThinkCentre M715q, card 1 `HD-Audio Generic` = Realtek ALC233 with a **fixed internal speaker**
(pin `0x14`, "Speaker at Int"), playback device `/dev/snd/pcmC1D0p`. Mixer controls include
`Speaker Playback Switch/Volume`. Card 0 is HDMI only. ALSA userland exists on the host; the
container brings its own.

## 3. Sounds

### 3.1 Built-ins (bundled in the repo)

Converted once at development time from the user's files with ffmpeg to 16-bit mono 48 kHz WAV and
committed under `led_notify/sounds/` (a backup of the finals and the original MP3s lives in `HomeLab/chimes/`, outside the repo):

| Name | Source | Processing | Loudness (mean / peak) |
|---|---|---|---|
| `default` | `chime-default.mp3` (2.1 s) | **+9 dB**, limiter at −1 dBFS | −16.3 / −1.4 dB |
| `success` | `chime-sucess.mp3` (3.4 s) | none (format conversion only) | −23.2 / −4.8 dB |
| `error` | `error-chime.mp3` (1.1 s) | none (format conversion only) | −19.7 / −4.6 dB |

### 3.2 Uploads

- Accepted: WAV or MP3, ≤ 2 MB, ≤ 10 s. MP3 is decoded to WAV on upload (`mpg123` in the container).
- Stored in `/data/sounds/<name>.wav`; `<name>` = slug of the filename (`[a-z0-9_-]{1,40}`), must not
  collide with a built-in. Referenced in rules as `custom:<name>`.
- Deleting an upload that a rule uses resets those rules' `sound` to `default` (and logs it).

## 4. Rules and settings

- Each rule gains `sound`: `"default" | "success" | "error" | "custom:<name>" | null` (null = silent).
  Settings validation rejects unknown sounds.
- Default rule sounds:

| Rule | Sound |
|---|---|
| `internet.down`, `dns.down`, `disk.smart_error`, `device.new_late`, `container.died` | `error` |
| `internet.up`, `device.known_arrived` | `success` |
| `device.new`, `internet.slow`, `disk.full`, `cpu.hot` | `default` |
| `adguard.block_spike` | `null` |

- New settings section `audio`:
  `{"enabled": true, "volume_pct": 70, "night_volume_pct": 30, "device": "plughw:1,0"}`
  (`volume_pct`/`night_volume_pct` 0–100).
- Webhook events may carry `"sound": "<name>"` (validated like `pattern`; ad-hoc events without one
  use `default`).
- Existing `settings.json` files gain the new keys from defaults (existing deep-merge behaviour); existing
  rules without `sound` get the default table above merged in.

## 5. When a chime plays

The **Arbiter** decides, so sound follows the light exactly:

- **One-shot**: when its light starts playing (`shown`).
- **Persistent state**: the first time it becomes the displayed look. Not again while it stays active;
  not if it was hidden by a higher-priority state the whole time it was active.
- **Recovery**: via the clear's follow-up one-shot (e.g. `internet.up` → `success`).
- **Test / Force**: always chime (forced looks), at day volume. Gallery pattern previews are silent unless the preview request names a `sound`; sounds are previewed with their own ▶ button.
- **Quiet hours**: only rules with priority `critical` chime, at `night_volume_pct`; everything else
  is silent at night regardless of its light behaviour.
- **Manual mode** and the idle look never chime.

The arbiter hands `(sound_ref, volume_pct)` to an `AudioPlayer`; it never awaits playback.

## 6. Components

| Unit | Responsibility |
|---|---|
| `led_notify/sounds/` | bundled `default.wav`, `success.wav`, `error.wav` |
| `audio/library.py` — `SoundLibrary(data_dir)` | list built-ins + uploads, resolve a ref to a WAV path, validate/import uploads (size, duration, format; MP3 → WAV), delete |
| `audio/player.py` — `AudioPlayer` | bounded queue (max 3; drop oldest), plays one sound at a time: read WAV, scale samples by volume in Python, pipe to `aplay -q -D <device> -`; 15 s timeout per play; on startup unmutes `Speaker` and sets it and `Master` to 100 % via `amixer -c 1` (volume is applied in software); errors logged, never raised to callers; `status()` for the UI |
| `audio/fake.py` — `FakeAudioPlayer` | records plays, for tests |
| Arbiter changes | pick the chime per §5 and call `audio.play(ref, volume)` |
| API | `GET /api/sounds`; `PUT /api/sounds/{name}` (raw file body, `Content-Type` audio/wav or audio/mpeg); `DELETE /api/sounds/{name}`; `POST /api/sounds/{ref}/play` (preview at day volume); `sound` on `/api/events` and `/api/preview`; audio status in `/api/state` |
| UI | Rules table gains a **Sound** column (select + ▶ preview); Settings gains an **Audio** box (enabled, volume, night volume, upload, list with delete) |
| Container | `alsa-utils`, `mpg123` in the image; compose adds the `/dev/snd` device (the container runs as root, so no extra group is needed) |

## 7. Error handling

- Missing/corrupt sound file at play time → log, skip (no fallback noise).
- `aplay` failure or timeout → log, mark audio status `error: <msg>` in `/api/state`, keep serving.
- Queue overflow (burst of events) → drop oldest pending chime.
- Upload errors → 422 with a message (too big, too long, unsupported format, bad name, built-in name).

## 8. Testing

- Unit: library (built-ins present, ref resolution, upload validation incl. duration/size/name rules, MP3
  path with a fake decoder, delete resets rules), player (volume scaling math, queue overflow, timeout,
  error isolation — with a fake `aplay`), arbiter (which events chime per §5 incl. state-hidden,
  recovery, night critical-only + night volume, manual/idle silent, force), settings validation for
  `sound` and `audio`, API (upload/list/delete/play, webhook `sound`, 422 paths).
- Hardware: play each built-in on the real speaker at a reasonable hour and confirm loudness.

## 9. Out of scope

Text-to-speech, repeating chimes while a state is active, per-rule volume, HDMI/external speakers,
scheduling chimes outside events.
