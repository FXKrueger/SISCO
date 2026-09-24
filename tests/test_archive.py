import json
import tempfile
from compression import zstd
from pathlib import Path

from ingestion.archive import Archive


def rows(root):
    return [json.loads(line) for p in sorted(Path(root).rglob("*.jsonl.zst")) for line in zstd.open(p, "rt")]


def test_roundtrip_append_and_crash_safety():
    with tempfile.TemporaryDirectory() as root:
        a = Archive(root)
        a.write("okx", '{"x":1}')
        a.write("okx", meta="connected")
        a.flush()
        # Readable after flush while the file is still open (process could die here).
        assert [r.get("data") or r.get("meta") for r in rows(root)] == ['{"x":1}', "connected"]
        a.close()

        # A restart appends a new frame to the same hour file. Nothing is overwritten.
        b = Archive(root)
        b.write("okx", {"body": "é"})
        b.close()
        got = rows(root)
        assert len(got) == 3 and got[2]["data"] == {"body": "é"}
        assert all(r["source"] == "okx" and isinstance(r["ingested_at"], int) for r in got)
        assert len(list(Path(root).rglob("*.jsonl.zst"))) == 1


if __name__ == "__main__":
    test_roundtrip_append_and_crash_safety()
    print("ok")
