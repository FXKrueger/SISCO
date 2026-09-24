"""Bluesky posts via Jetstream (about 50 posts/s, about 0.7 GB/day compressed).

On reconnect we resume from the last seen cursor minus 10 s, so short gaps are backfilled.
Backfilled posts get their real (later) ingested_at, which is correct point-in-time.
"""

import json

from .ws import stream

URL = "wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post"


async def run(archive):
    cursor = [None]

    def track(msg):
        cursor[0] = json.loads(msg).get("time_us", cursor[0])

    def url():
        return URL + (f"&cursor={cursor[0] - 10_000_000}" if cursor[0] else "")

    await stream(archive, "bluesky", url, stale=120, on_msg=track)
