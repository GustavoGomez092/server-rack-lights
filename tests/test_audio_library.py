import array
import io
import wave

import pytest

from led_notify.audio.library import SoundError, SoundLibrary, slugify


def make_wav(seconds=1.0, width=2, rate=8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        n = int(seconds * rate)
        w.writeframes(array.array("h", [0] * n).tobytes() if width == 2 else bytes(n))
    return buf.getvalue()


@pytest.fixture
def lib(tmp_path):
    return SoundLibrary(tmp_path, decode_mp3=lambda data: make_wav(1.5))


def test_builtins_are_bundled_and_listed(lib):
    items = {i["ref"]: i for i in lib.list()}
    assert set(items) == {"default", "success", "error"}
    assert all(i["builtin"] for i in items.values())
    assert 4.9 < items["default"]["duration_s"] < 5.2
    assert 0.9 < items["error"]["duration_s"] < 1.3
    assert lib.exists("success") and lib.resolve("success").name == "success.wav"


def test_resolve_unknown_and_invalid(lib):
    assert lib.resolve("custom:nothing") is None and lib.resolve("nope") is None and lib.resolve(None) is None


def test_upload_wav_then_list_resolve_delete(lib):
    item = lib.import_upload("door.wav", make_wav(1.0), "audio/wav")
    assert item == {"ref": "custom:door", "name": "door", "builtin": False, "duration_s": 1.0}
    assert lib.exists("custom:door")
    assert "custom:door" in [i["ref"] for i in lib.list()]
    assert lib.delete("door") is True and lib.delete("door") is False
    assert not lib.exists("custom:door")


def test_upload_mp3_is_decoded(lib):
    item = lib.import_upload("ring.mp3", b"ID3fake", "audio/mpeg")
    assert item["ref"] == "custom:ring" and item["duration_s"] == 1.5


def test_upload_name_is_slugged(lib):
    assert slugify("My Chime (1).MP3") == "my-chime-1"
    assert lib.import_upload("My Chime (1).wav", make_wav(0.5), "audio/x-wav")["ref"] == "custom:my-chime-1"


def test_upload_rejects_builtin_name(lib):
    with pytest.raises(SoundError, match="built-in"):
        lib.import_upload("default.wav", make_wav(1.0), "audio/wav")


@pytest.mark.parametrize("name,data,ctype,msg", [
    ("big.wav", b"x" * (2 * 1024 * 1024 + 1), "audio/wav", "2 MB"),
    ("long.wav", make_wav(11.0), "audio/wav", "10 seconds"),
    ("eight.wav", make_wav(1.0, width=1), "audio/wav", "16-bit"),
    ("junk.wav", b"not a wav", "audio/wav", "valid WAV"),
    ("pic.png", make_wav(1.0), "image/png", "WAV or MP3"),
    ("!!!.wav", make_wav(1.0), "audio/wav", "name"),
])
def test_upload_rejections(lib, name, data, ctype, msg):
    with pytest.raises(SoundError, match=msg):
        lib.import_upload(name, data, ctype)


def test_mp3_decoder_failure_is_a_sound_error(tmp_path):
    def broken(data):
        raise SoundError("could not decode the MP3 file")
    with pytest.raises(SoundError, match="decode"):
        SoundLibrary(tmp_path, decode_mp3=broken).import_upload("x.mp3", b"x", "audio/mpeg")
