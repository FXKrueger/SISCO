"""Trial registry (SPEC 12.2 #3). Append-only, written by the harness only.

Each line carries the hash of the previous line, so an edited or deleted entry breaks the chain
(verify()). Daily R series are kept per trial for PBO.
ponytail: a local file. Truncating the tail is not detectable from the file alone; the server
copy and backups are the real record once they exist (issues #1, #2).
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(os.environ.get("SISCO_DATA", "data")) / "registry"
LOG = ROOT / "trials.jsonl"


def _hash(line):
    return hashlib.sha256(line.encode()).hexdigest()


def entries():
    return [json.loads(x) for x in LOG.read_text().splitlines()] if LOG.exists() else []


def verify():
    prev = "0" * 64
    for i, line in enumerate(LOG.read_text().splitlines() if LOG.exists() else []):
        e = json.loads(line)
        if e["prev"] != prev:
            raise RuntimeError(f"registry chain broken at entry {i}")
        prev = _hash(line)
    return prev


def count(hypothesis):
    return sum(e["hypothesis"] == hypothesis for e in entries())


def append(record, series=None):
    """record: dict. series: {name: daily pd.Series} saved for PBO. Returns the trial id."""
    ROOT.mkdir(parents=True, exist_ok=True)
    prev = verify()
    trial_id = f"T{len(entries()) + 1:05d}"
    rec = {"trial_id": trial_id, "time": datetime.now(timezone.utc).isoformat(), **record, "prev": prev}
    with LOG.open("a") as f:
        f.write(json.dumps(rec, default=str, sort_keys=True) + "\n")
    if series is not None:
        (ROOT / "series").mkdir(exist_ok=True)
        pd.DataFrame(series).to_parquet(ROOT / "series" / f"{trial_id}.parquet")
    return trial_id


def series(hypothesis, name="net_2x"):
    """days x trials matrix of one series for all trials of a hypothesis."""
    cols = {}
    for e in entries():
        p = ROOT / "series" / f"{e['trial_id']}.parquet"
        if e["hypothesis"] == hypothesis and p.exists():
            cols[e["trial_id"]] = pd.read_parquet(p)[name]
    return pd.DataFrame(cols).fillna(0.0)
