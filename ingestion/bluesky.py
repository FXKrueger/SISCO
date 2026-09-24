"""Bluesky posts via Jetstream (about 50 posts/s, about 0.7 GB/day compressed).

On reconnect, and on start after a pause, we resume from the last archived cursor minus 10 s.
Jetstream keeps about 72 h, so pauses shorter than that lose nothing (D19, on-demand running).
Backfilled posts get their real (later) ingested_at, which is correct point-in-time.
"""

import json

from .archive import read_rows
from .ws import stream

URL = "wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post"


async def run(archive):
    cursor = [None]
    for p in sorted((archive.root / "bluesky").rglob("*.jsonl.zst"), reverse=True)[:2]:
        rows = [r for r in read_rows(p) if "data" in r]
        if rows:
            cursor[0] = json.loads(rows[-1]["data"]).get("time_us")
            break

    def track(msg):
        cursor[0] = json.loads(msg).get("time_us", cursor[0])

    def url():
        return URL + (f"&cursor={cursor[0] - 10_000_000}" if cursor[0] else "")

    await stream(archive, "bluesky", url, stale=120, on_msg=track)
