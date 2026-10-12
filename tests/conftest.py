import pytest

from tests.fakes import FakeClock


@pytest.fixture
def clock():
    return FakeClock()
