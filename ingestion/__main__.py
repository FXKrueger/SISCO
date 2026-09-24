"""Day-1 archives (SPEC 5.3).

  python -m ingestion                  run all collectors
  python -m ingestion text bluesky     run some
  python -m ingestion health           exit 1 if any source has been silent too long
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from . import bluesky, liquidations, okx_xperps, text
from .archive import Archive

COLLECTORS = {"okx_xperps": okx_xperps.run, "liquidations": liquidations.run, "text": text.run, "bluesky": bluesky.run}
DATA = Path(os.environ.get("SISCO_DATA", "data"))
STATUS = DATA / "status.json"
STALE_S = 600  # quiet liquidation feeds still pong every 25 s, pollers report each poll

log = logging.getLogger("ingestion")


async def supervise(name, run, archive):
    while True:
        try:
            await run(archive)
        except Exception:
            log.exception("%s crashed, restarting in 30s", name)
            await asyncio.sleep(30)


async def status_loop(archive):
    while True:
        await asyncio.sleep(30)
        archive.flush()
        STATUS.write_text(json.dumps({s: ns // 10**9 for s, ns in sorted(archive.last_seen.items())}, indent=1))


async def main(names):
    archive = Archive(DATA / "raw")
    try:
        await asyncio.gather(status_loop(archive), *(supervise(n, COLLECTORS[n], archive) for n in names))
    finally:
        archive.close()


def health():
    last = json.loads(STATUS.read_text())
    stale = {s: int(time.time() - t) for s, t in last.items() if time.time() - t > STALE_S}
    if stale or time.time() - STATUS.stat().st_mtime > 120:
        print("stale:", stale or "status.json")
        sys.exit(1)
    print(f"ok, {len(last)} sources")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = sys.argv[1:]
    if args == ["health"]:
        health()
    else:
        try:
            asyncio.run(main(args or list(COLLECTORS)))
        except KeyboardInterrupt:
            pass
