import pytest

from tests.fakes import FakeClock


@pytest.fixture
def clock():
    return FakeClock()


from led_notify.store.settings import SettingsStore


@pytest.fixture
def settings(tmp_path):
    return SettingsStore(tmp_path / "settings.json")
