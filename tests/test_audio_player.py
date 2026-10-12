import array
import asyncio
import io
import wave

import pytest

from led_notify.audio.fake import FakeChimes
from led_notify.audio.player import AudioPlayer, scale_wav
from led_notify.audio.service import AudioService


def make_wav(samples, width=2, rate=8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 2:
            w.writeframes(array.array("h", samples).tobytes())
        else:
            w.writeframes(bytes(samples))
    return buf.getvalue()


def samples_of(data: bytes):
    with wave.open(io.BytesIO(data)) as w:
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
        return list(a)


def test_scale_wav_volume():
    src = make_wav([1000, -2000, 32767, -32768])
    assert samples_of(scale_wav(src, 100)) == [1000, -2000, 32767, -32768]
    assert samples_of(scale_wav(src, 50)) == [500, -1000, 16383, -16384]
    assert samples_of(scale_wav(src, 0)) == [0, 0, 0, 0]
    assert samples_of(scale_wav(src, 150)) == [1000, -2000, 32767, -32768]   # clamped to 100 %


def test_scale_wav_rejects_8_bit():
    with pytest.raises(ValueError):
        scale_wav(make_wav([1, 2, 3], width=1), 50)


class Recorder:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def __call__(self, device, wav, timeout):
        self.calls.append((device, samples_of(wav), timeout))
        if self.fail:
            raise self.fail


async def test_play_now_scales_and_uses_device(tmp_path):
    p = tmp_path / "s.wav"
    p.write_bytes(make_wav([1000, -1000]))
    rec = Recorder()
    player = AudioPlayer("plughw:1,0", runner=rec)
    await player.play_now(p, 50)
    assert rec.calls == [("plughw:1,0", [500, -500], 15.0)]
    assert player.status()["played"] == 1 and player.status()["last_error"] == ""


async def test_runner_failure_is_isolated_and_reported(tmp_path):
    p = tmp_path / "s.wav"
    p.write_bytes(make_wav([1]))
    player = AudioPlayer("plughw:1,0", runner=Recorder(fail=RuntimeError("aplay failed: no such device")))
    await player.play_now(p, 70)                     # must not raise
    assert "no such device" in player.status()["last_error"]
    await player.play_now(tmp_path / "missing.wav", 70)
    assert "FileNotFoundError" in player.status()["last_error"]


async def test_queue_overflow_keeps_newest_three(tmp_path):
    player = AudioPlayer("d", runner=Recorder())
    for i in range(5):
        player.enqueue(tmp_path / f"{i}.wav", 70)
    assert player.status()["queued"] == 3
    names = [player._queue.get_nowait()[0].name for _ in range(3)]
    assert names == ["2.wav", "3.wav", "4.wav"]


async def test_run_plays_queued_sounds_in_order(tmp_path):
    for i in range(2):
        (tmp_path / f"{i}.wav").write_bytes(make_wav([i * 100 + 100]))
    rec = Recorder()
    player = AudioPlayer("d", runner=rec)
    player.enqueue(tmp_path / "0.wav", 100)
    player.enqueue(tmp_path / "1.wav", 100)
    task = asyncio.create_task(player.run())
    for _ in range(50):
        await asyncio.sleep(0)
        if len(rec.calls) == 2:
            break
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert [c[1] for c in rec.calls] == [[100], [200]]


class Lib:
    def __init__(self, path):
        self.path = path

    def resolve(self, ref):
        return self.path if ref == "default" else None


async def test_service_chime_resolves_and_enqueues(tmp_path):
    player = AudioPlayer("d", runner=Recorder())
    svc = AudioService(Lib(tmp_path / "d.wav"), player)
    assert svc.chime("default", 70) is True and player.status()["queued"] == 1
    assert svc.chime("custom:gone", 70) is False and player.status()["queued"] == 1
    assert svc.status()["device"] == "d"


def test_fake_chimes_records():
    f = FakeChimes()
    assert f.chime("error", 30) is True and f.chimes == [("error", 30)]
