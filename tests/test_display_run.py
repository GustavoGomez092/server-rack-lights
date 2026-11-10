"""display/run.sh against stand-ins for seatd, cage, wlr-randr and curl."""
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

RUN = Path(__file__).parent.parent / "display" / "run.sh"
LOOP = Path(__file__).parent.parent / "display" / "screen-loop.sh"

FAKES = {
    "seatd": "exec sleep 1000\n",
    # Like cage 0.2: runs its client and, on SIGTERM, waits for that client to exit (it never kills it).
    "cage": """\
echo "cage $*" >> "$FAKE/log"; echo yes > "$FAKE/enabled"
shift; "$@" & child=$!
trap 'wait $child; exit 0' TERM
while true; do sleep 0.1; done
""",
    "rack-screen": "exec sleep 1000\n",
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
               "XDG_RUNTIME_DIR": str(tmp_path / "xdg"), "DRM_STATUS": str(tmp_path / "status"),
               "SCREEN_BIN": str(bin_ / "rack-screen")}
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


def test_night_never_cuts_the_signal(fake, run):
    # The rack monitor falls asleep without a signal and only a power cycle wakes it, so "off" is a black
    # page (drawn by the status page itself); the output must stay enabled.
    (fake / "api").write_text('{"power":"off","theme":"dark"}')
    (fake / "page-ok").touch()
    run()
    assert wait_until(lambda: any(line.startswith("cage") for line in log(fake)), 3), log(fake)
    assert any(line.endswith("screen-loop.sh") for line in log(fake)), log(fake)  # cage runs the restart loop
    time.sleep(6.5)
    assert not any("--off" in line for line in log(fake)), log(fake)


def test_a_disabled_output_is_turned_back_on(fake, run):
    (fake / "page-ok").touch()
    run()
    assert wait_until(lambda: any(line.startswith("cage") for line in log(fake)), 3), log(fake)
    (fake / "enabled").write_text("no\n")
    assert wait_until(lambda: "--on DP-1" in log(fake), 7), log(fake)


def test_kiosk_waits_for_led_notify_page(fake, run):
    (fake / "api").write_text('{"power":"on"}')
    run()
    time.sleep(2)
    assert not any(line.startswith("cage") for line in log(fake))  # never show Chromium's error page on the monitor
    (fake / "page-ok").touch()
    assert wait_until(lambda: any(line.startswith("cage") for line in log(fake)), 7), log(fake)


def test_sigterm_stops_quickly(fake, run):
    (fake / "api").write_text('{"power":"on"}')
    (fake / "page-ok").touch()
    p = run()
    assert wait_until(lambda: any(line.startswith("cage") for line in log(fake)), 3)
    t0 = time.time()
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=5)
    assert time.time() - t0 < 2


def test_screen_loop_restarts_the_program(tmp_path):
    # cage exits when its client does, and a cage restart could drop the monitor's signal; so cage runs this
    # loop, and only the screen program restarts.
    prog = tmp_path / "prog"
    prog.write_text(f'#!/bin/bash\necho run >> "{tmp_path}/runs"\nexit 1\n')
    prog.chmod(0o755)
    p = subprocess.Popen(["bash", str(LOOP)], env={**os.environ, "SCREEN_BIN": str(prog)},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        assert wait_until(lambda: (tmp_path / "runs").exists() and
                          len((tmp_path / "runs").read_text().split()) >= 2, 4)
        assert p.poll() is None
    finally:
        with_group_kill(p)


def test_unplug_then_replug_brings_the_screen_back(fake, run):
    (fake / "page-ok").touch()
    run()
    starts = lambda: sum(line.startswith("cage") for line in log(fake))
    assert wait_until(lambda: starts() == 1, 3), log(fake)
    (fake / "status").write_text("disconnected\n")
    time.sleep(6)
    (fake / "status").write_text("connected\n")
    assert wait_until(lambda: starts() == 2, 12), log(fake)
