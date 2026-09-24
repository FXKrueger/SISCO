"""Raw archive: append-only, hourly zstd JSONL files. Nothing is ever overwritten.

Layout: <root>/<source>/<YYYY-MM-DD>/<HH>.jsonl.zst (UTC hour of ingestion).
Row: {"ingested_at": <unix ns>, "source": str, "data": <message as received>}
  or {"ingested_at": ..., "source": ..., "meta": "connected" | "disconnected: ..." | ...}

Raw rows only carry ingested_at and source. For raw data, available_time = ingested_at.
event_time and revision are derived when the raw rows are normalized into the PIT store (M1).
Meta rows mark connects and drops, so gaps in coverage are visible later.
"""

import json
import time
from compression import zstd
from pathlib import Path


class Archive:
    def __init__(self, root, flush_every=5.0):
        self.root = Path(root)
        self.flush_every = flush_every
        self.files = {}  # source -> (hour key, open ZstdFile)
        self.last_seen = {}  # source -> unix ns of the last sign of life (data, pong, successful poll)
        self._last_flush = time.monotonic()

    def write(self, source, data=None, meta=None):
        ns = time.time_ns()
        key = time.strftime("%Y-%m-%d/%H", time.gmtime(ns // 10**9))
        cur = self.files.get(source)
        if cur is None or cur[0] != key:
            if cur:
                cur[1].close()
            path = self.root / source / f"{key}.jsonl.zst"
            path.parent.mkdir(parents=True, exist_ok=True)
            # "a" adds a new zstd frame, so a restart inside the same hour keeps earlier rows.
            cur = self.files[source] = (key, zstd.ZstdFile(path, "a"))
        row = {"ingested_at": ns, "source": source}
        if meta is None:
            row["data"] = data
            self.last_seen[source] = ns
        else:
            row["meta"] = meta
        cur[1].write(json.dumps(row).encode() + b"\n")
        if time.monotonic() - self._last_flush > self.flush_every:
            self.flush()

    def seen(self, source):
        self.last_seen[source] = time.time_ns()

    def flush(self):
        # A closed frame is readable even if the process dies right after. Max loss: flush_every.
        for _, f in self.files.values():
            f.flush(zstd.ZstdFile.FLUSH_FRAME)
        self._last_flush = time.monotonic()

    def close(self):
        for _, f in self.files.values():
            f.close()
        self.files.clear()
