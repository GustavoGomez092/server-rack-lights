"""Plays WAV chimes through ALSA, one at a time, never blocking or failing callers."""
from __future__ import annotations

import array
import asyncio
import io
import logging
import re
import sys
import wave
from pathlib import Path

log = logging.getLogger(__name__)

QUEUE_MAX = 3
PLAY_TIMEOUT_S = 15.0
MIXER_TIMEOUT_S = 5.0


def scale_wav(data: bytes, volume_pct: int) -> bytes:
    """Return the WAV with its 16-bit samples scaled to volume_pct (clamped to 0-100)."""
    factor = max(0, min(100, int(volume_pct))) / 100
    with wave.open(io.BytesIO(data)) as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    if params.sampwidth != 2:
        raise ValueError("only 16-bit PCM WAV is supported")
    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()
    if factor != 1:
        for i, s in enumerate(samples):
            samples[i] = int(s * factor)
    if sys.byteorder == "big":
        samples.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(samples.tobytes())
    return out.getvalue()


async def run_aplay(device: str, wav: bytes, timeout: float) -> None:
    proc = await asyncio.create_subprocess_exec(
        "aplay", "-q", "-D", device, "-",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        try:
            _, err = await asyncio.wait_for(proc.communicate(wav), timeout)
        except TimeoutError:
            raise RuntimeError(f"aplay timed out after {timeout:.0f} s")
        if proc.returncode != 0:
            raise RuntimeError(f"aplay failed: {err.decode(errors='replace').strip()[:200]}")
    finally:
        if proc.returncode is None:  # timeout or cancellation: never leave aplay holding the device
            proc.kill()
            await proc.wait()


async def silent_runner(device: str, wav: bytes, timeout: float) -> None:
    """Used with --fake-strip / --no-audio: accept the sound and play nothing."""
    return None


async def init_mixer(device: str) -> None:
    """Unmute the internal speaker and set it to full; volume is applied in software per chime.

    Never raises and never blocks startup for more than MIXER_TIMEOUT_S per control.
    """
    m = re.search(r":(\d+)", device)
    card = m.group(1) if m else "0"
    for control in ("Master", "Speaker"):
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-q", "-c", card, "sset", control, "100%", "unmute",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), MIXER_TIMEOUT_S)
            if proc.returncode != 0:
                log.warning("amixer could not set %s on card %s (exit %s)", control, card, proc.returncode)
        except FileNotFoundError:
            log.warning("amixer not found; leaving mixer as is")
            return
        except TimeoutError:
            log.warning("amixer timed out setting %s on card %s", control, card)
        except Exception as e:
            log.warning("amixer failed for %s on card %s: %s: %s", control, card, type(e).__name__, e)
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), MIXER_TIMEOUT_S)
                except Exception:
                    pass


class AudioPlayer:
    def __init__(self, device: str, runner=run_aplay):
        self.device = device
        self.runner = runner
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.played = 0
        self.last_error = ""

    def enqueue(self, path, volume_pct: int) -> None:
        if self._queue.full():
            self._queue.get_nowait()  # a burst keeps only the newest chimes
        self._queue.put_nowait((Path(path), int(volume_pct)))

    async def play_now(self, path, volume_pct: int) -> None:
        try:
            wav = scale_wav(Path(path).read_bytes(), volume_pct)
            await self.runner(self.device, wav, PLAY_TIMEOUT_S)
            self.played += 1
            self.last_error = ""
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("chime failed: %s", self.last_error)

    async def run(self) -> None:
        while True:
            path, volume = await self._queue.get()
            await self.play_now(path, volume)

    def status(self) -> dict:
        return {"device": self.device, "played": self.played, "last_error": self.last_error,
                "queued": self._queue.qsize()}
