"""Built-in chimes plus user uploads, all stored as 16-bit WAV."""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
import wave
from pathlib import Path

from .refs import BUILTINS, CUSTOM_PREFIX, NAME_RE, is_valid_ref

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "sounds"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_DURATION_S = 10.0
WAV_TYPES = ("audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave")
MP3_TYPES = ("audio/mpeg", "audio/mp3")


class SoundError(ValueError):
    pass


def slugify(filename: str) -> str:
    stem = Path(filename).stem.lower()
    return re.sub(r"[^a-z0-9_-]+", "-", stem).strip("-")[:40]


def _wav_info(data: bytes) -> tuple[float, int]:
    """(duration seconds, sample width bytes)."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / w.getframerate(), w.getsampwidth()
    except (wave.Error, EOFError, ZeroDivisionError) as e:
        raise SoundError(f"not a valid WAV file ({e})")


def mpg123_decode(data: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d, "in.mp3"), Path(d, "out.wav")
        src.write_bytes(data)
        try:
            subprocess.run(["mpg123", "-q", "-w", str(dst), str(src)], check=True, timeout=20,
                           capture_output=True)
        except FileNotFoundError:
            raise SoundError("MP3 support needs mpg123, which is not installed")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise SoundError("could not decode the MP3 file")
        return dst.read_bytes()


class SoundLibrary:
    def __init__(self, data_dir, decode_mp3=mpg123_decode, builtin_dir: Path = BUILTIN_DIR):
        self.dir = Path(data_dir) / "sounds"
        self.builtin_dir = Path(builtin_dir)
        self.decode_mp3 = decode_mp3

    def resolve(self, ref) -> Path | None:
        if not is_valid_ref(ref):
            return None
        if ref in BUILTINS:
            path = self.builtin_dir / f"{ref}.wav"
        else:
            path = self.dir / f"{ref[len(CUSTOM_PREFIX):]}.wav"
        return path if path.is_file() else None

    def exists(self, ref) -> bool:
        return self.resolve(ref) is not None

    def list(self) -> list[dict]:
        items = [self._item(name, name, True, p) for name in BUILTINS if (p := self.resolve(name))]
        if self.dir.is_dir():
            for p in sorted(self.dir.glob("*.wav")):
                if NAME_RE.match(p.stem):
                    items.append(self._item(CUSTOM_PREFIX + p.stem, p.stem, False, p))
        return items

    @staticmethod
    def _item(ref: str, name: str, builtin: bool, path: Path) -> dict:
        try:
            duration = round(_wav_info(path.read_bytes())[0], 2)
        except (SoundError, OSError):
            duration = None
        return {"ref": ref, "name": name, "builtin": builtin, "duration_s": duration}

    def import_upload(self, filename: str, data: bytes, content_type: str) -> dict:
        name = slugify(filename or "")
        if not NAME_RE.match(name):
            raise SoundError("name must be 1-40 characters: a-z, 0-9, '-' or '_'")
        if name in BUILTINS:
            raise SoundError(f"{name!r} is a built-in sound name")
        if len(data) > MAX_UPLOAD_BYTES:
            raise SoundError("file is larger than 2 MB")
        ctype = (content_type or "").split(";")[0].strip().lower()
        if ctype in MP3_TYPES:
            data = self.decode_mp3(data)
        elif ctype not in WAV_TYPES:
            raise SoundError("upload a WAV or MP3 file")
        duration, width = _wav_info(data)
        if width != 2:
            raise SoundError("WAV must be 16-bit PCM")
        if duration > MAX_DURATION_S:
            raise SoundError("sound is longer than 10 seconds")
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{name}.tmp"
        tmp.write_bytes(data)
        tmp.replace(self.dir / f"{name}.wav")
        return self._item(CUSTOM_PREFIX + name, name, False, self.dir / f"{name}.wav")

    def delete(self, name: str) -> bool:
        if not NAME_RE.match(name or ""):
            return False
        path = self.dir / f"{name}.wav"
        if not path.is_file():
            return False
        path.unlink()
        return True
