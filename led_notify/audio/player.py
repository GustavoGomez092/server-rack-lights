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
        _, err = await asyncio.wait_for(proc.communicate(wav), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"aplay timed out after {timeout:.0f} s")
    if proc.returncode != 0:
        raise RuntimeError(f"aplay failed: {err.decode(errors='replace').strip()[:200]}")


async def silent_runner(device: str, wav: bytes, timeout: float) -> None:
    """Used with --fake-strip / --no-audio: accept the sound and play nothing."""
    return None


async def init_mixer(device: str) -> None:
    """Unmute the internal speaker and set it to full; volume is applied in software per chime."""
    m = re.search(r":(\d+)", device)
    card = m.group(1) if m else "0"
    for control in ("Master", "Speaker"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-q", "-c", card, "sset", control, "100%", "unmute",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except FileNotFoundError:
            log.warning("amixer not found; leaving mixer as is")
            return


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
