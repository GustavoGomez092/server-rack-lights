import array
import asyncio
import io
import wave

import pytest

from led_notify.audio.fake import FakeChimes
from led_notify.audio.player import AudioPlayer, scale_wav, silent_runner
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


async def test_device_callable_is_resolved_per_play(tmp_path):
    p = tmp_path / "s.wav"
    p.write_bytes(make_wav([1000]))
    rec = Recorder()
    current = {"device": "plughw:1,0"}
    player = AudioPlayer(lambda: current["device"], runner=rec)
    await player.play_now(p, 100)
    assert player.status()["device"] == "plughw:1,0"
    current["device"] = "plughw:2,0"                      # settings changed: no restart needed
    await player.play_now(p, 100)
    assert [c[0] for c in rec.calls] == ["plughw:1,0", "plughw:2,0"]
    assert player.status()["device"] == "plughw:2,0"


def test_status_mode():
    assert AudioPlayer("d", runner=Recorder()).status()["mode"] == "speaker"
    assert AudioPlayer("d", runner=silent_runner).status()["mode"] == "silent"


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


class FakeProc:
    """Stands in for an asyncio subprocess whose wait()/communicate() block until killed."""

    def __init__(self, returncode=None, hang=True):
        self.returncode = returncode
        self.hang = hang
        self.killed = False
        self._dead = asyncio.Event()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._dead.set()

    async def wait(self):
        if self.hang:
            await self._dead.wait()
        return self.returncode

    async def communicate(self, data=None):
        await self._dead.wait()
        return b"", b""


async def test_init_mixer_swallows_spawn_errors(monkeypatch):
    from led_notify.audio import player as player_mod

    async def boom(*a, **k):
        raise PermissionError("nope")

    monkeypatch.setattr(player_mod.asyncio, "create_subprocess_exec", boom)
    await player_mod.init_mixer("plughw:1,0")      # must not raise


async def test_init_mixer_times_out_hung_amixer_and_kills_it(monkeypatch):
    from led_notify.audio import player as player_mod

    procs = []

    async def spawn(*a, **k):
        procs.append(FakeProc())
        return procs[-1]

    monkeypatch.setattr(player_mod.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(player_mod, "MIXER_TIMEOUT_S", 0.05)
    await asyncio.wait_for(player_mod.init_mixer("plughw:1,0"), 2)
    assert procs and all(p.killed for p in procs)


async def test_init_mixer_logs_nonzero_exit(monkeypatch, caplog):
    from led_notify.audio import player as player_mod

    async def spawn(*a, **k):
        return FakeProc(returncode=1, hang=False)

    monkeypatch.setattr(player_mod.asyncio, "create_subprocess_exec", spawn)
    with caplog.at_level("WARNING"):
        await player_mod.init_mixer("plughw:1,0")
    assert "could not set Speaker on card 1 (exit 1)" in caplog.text


async def test_run_aplay_kills_child_on_cancel(monkeypatch):
    from led_notify.audio import player as player_mod

    proc = FakeProc()

    async def spawn(*a, **k):
        return proc

    monkeypatch.setattr(player_mod.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(player_mod.run_aplay("d", b"x", 15.0))
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert proc.killed
