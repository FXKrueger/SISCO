"""One-time setup checks.

  python -m ops.init --paper-equity 10000    set the paper account's starting equity (USD)
  python -m ops.init                         check the setup

Starting equity lives in the local journal (data/journal.db), never in the repo.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

from execution import okx
from risk.engine import load_limits

from .journal import Journal
from .session import ENV_FILE, load_env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper-equity", type=float)
    a = ap.parse_args()
    j = Journal()
    if a.paper_equity:
        if j.trades("mode = 'paper'"):
            sys.exit("paper trades exist already; the starting equity cannot change")
        j.set("paper_equity_start", a.paper_equity)
        print(f"paper starting equity: {a.paper_equity:.2f} USD")
    ok = True

    def say(good, msg):
        nonlocal ok
        ok &= good
        print(("OK   " if good else "FAIL ") + msg)

    limits = load_limits()
    say(True, f"mode in config/limits.yaml: {limits['mode']}, approved strategies: {len(limits['strategies'])}")
    say(j.get("paper_equity_start") is not None, "paper starting equity set (python -m ops.init --paper-equity N)")
    drift = abs(okx.server_time_ms() - time.time() * 1000)
    say(drift < 2000, f"clock within 2 s of the exchange ({drift:.0f} ms)")
    say(bool(okx.xperp_instruments()), "OKX X-Perps instruments reachable")
    say(subprocess.run(["git", "fetch", "-q", "origin", "main"]).returncode == 0, "git can fetch origin/main (strategy registration check)")
    say(os.path.exists("data/store/binance_um/klines_1h"), "research store present (python -m ingestion.binance_history)")
    say(shutil.which("docker") is not None, "docker installed (archives)")
    if limits["mode"] in ("demo", "live"):
        load_env()
        say(ENV_FILE.exists(), f"{ENV_FILE} exists with SISCO_OKX_KEY, SISCO_OKX_SECRET, SISCO_OKX_PASSPHRASE")
        try:
            b = okx.OkxBroker(demo=limits["mode"] == "demo")
            acc = b.account()
            say(True, f"OKX {limits['mode']} account reachable, equity {acc['equity']:.2f} USD")
        except Exception as e:
            say(False, f"OKX {limits['mode']} account: {e}")
    print("\nready" if ok else "\nfix the FAIL lines first")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
