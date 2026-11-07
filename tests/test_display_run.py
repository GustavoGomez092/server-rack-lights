"""display/run.sh against stand-ins for seatd, cage, wlr-randr and curl."""
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

RUN = Path(__file__).parent.parent / "display" / "run.sh"

FAKES = {
    "seatd": "exec sleep 1000\n",
    "cage": 'echo cage >> "$FAKE/log"; echo yes > "$FAKE/enabled"; exec sleep 1000\n',
    "wlr-randr": """\
if [ "$#" -ge 3 ]; then
  [ "$3" = --off ] && echo no > "$FAKE/enabled" || echo yes > "$FAKE/enabled"
  echo "$3 $2" >> "$FAKE/log"; exit 0
fi
[ -f "$FAKE/enabled" ] || exit 1
printf 'DP-1 "Monitor"\\n  Enabled: %s\\n' "$(cat "$FAKE/enabled")"
""",
    "curl": """\
for a in "$@"; do url=$a; done
case "$url" in
  */api/screen) [ -f "$FAKE/api" ] && cat "$FAKE/api" && exit 0; exit 7 ;;
  *) [ -f "$FAKE/page-ok" ] && exit 0; exit 7 ;;
esac
""",
}


@pytest.fixture
def run(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in FAKES.items():
        (bin_ / name).write_text("#!/bin/bash\n" + body)
        (bin_ / name).chmod(0o755)
    (tmp_path / "status").write_text("connected\n")
    procs = []

    def start():
        env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "FAKE": str(tmp_path), "POLL_S": "5",
               "XDG_RUNTIME_DIR": str(tmp_path / "xdg"), "DRM_STATUS": str(tmp_path / "status")}
        p = subprocess.Popen(["bash", str(RUN)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
        procs.append(p)
        return p

    yield start
    for p in procs:
        with_group_kill(p)


def with_group_kill(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    p.wait()


def log(fake):
    f = fake / "log"
    return f.read_text().split("\n") if f.exists() else []


def wait_until(cond, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def fake(tmp_path):
    return tmp_path


def test_night_start_turns_screen_off_within_a_second_or_two(fake, run):
    (fake / "api").write_text('{"power":"off","theme":"dark"}')
    (fake / "page-ok").touch()
    run()
    assert wait_until(lambda: "--off DP-1" in log(fake), 3.5), log(fake)


def test_kiosk_waits_for_led_notify_page(fake, run):
    (fake / "api").write_text('{"power":"on"}')
    run()
    time.sleep(2)
    assert "cage" not in log(fake)  # never show Chromium's error page on the monitor
    (fake / "page-ok").touch()
    assert wait_until(lambda: "cage" in log(fake), 7), log(fake)


def test_sigterm_stops_quickly(fake, run):
    (fake / "api").write_text('{"power":"on"}')
    (fake / "page-ok").touch()
    p = run()
    assert wait_until(lambda: "cage" in log(fake), 3)
    t0 = time.time()
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=5)
    assert time.time() - t0 < 2
