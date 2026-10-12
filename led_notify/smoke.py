"""Play every named pattern on the real strip through a running service.

    python -m led_notify.smoke --url http://192.168.4.61:8787
"""
from __future__ import annotations

import argparse
import time

import httpx

from .patterns import NAMED, Sequence, sequence_ms

HOLD_S = 4.0
GAP_S = 1.0


def run(client: httpx.Client, sleep=time.sleep) -> None:
    for name, pattern in NAMED.items():
        seconds = sequence_ms(pattern) / 1000 if isinstance(pattern, Sequence) else HOLD_S
        print(f"▶ {name:20s} ({seconds:.1f}s)", flush=True)
        client.post("/api/preview", json={"pattern": name, "duration": HOLD_S}).raise_for_status()
        sleep(seconds + GAP_S)
    print("Done — the strip returns to its normal state.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://192.168.4.61:8787")
    args = p.parse_args(argv)
    with httpx.Client(base_url=args.url, timeout=10) as client:
        run(client)


if __name__ == "__main__":
    main()
