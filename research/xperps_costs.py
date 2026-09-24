"""Measure X-Perps half-spread and impact from the raw book archive (input for the cost model).

  python -m research.xperps_costs [notional_usd]

Rebuilds each order book from snapshot + incremental updates, samples it once a minute and
reports per instrument: median half-spread and the cost of a market order of the given notional
(half-spread plus walking the book), both as a fraction of mid.
"""

import json
import os
import sys
from collections import defaultdict
from ingestion.archive import read_rows
from pathlib import Path

import pandas as pd

RAW = Path(os.environ.get("SISCO_DATA", "data")) / "raw" / "okx_xperps"


def main(notional=25_000):
    ct = {}  # contract value in base units, from the instruments snapshot
    inst = sorted((RAW.parent / "text" / "okx_eea_instruments").rglob("*.zst"))
    if inst:
        body = json.loads(next(r for r in read_rows(inst[-1]) if "data" in r)["data"]["body"])
        ct = {i["instId"]: float(i["ctVal"]) * float(i.get("ctMult") or 1) for i in body["data"]}
    books = defaultdict(lambda: ({}, {}))
    last_sample, rows = {}, []
    for p in sorted(RAW.rglob("*.jsonl.zst")):
        for r in read_rows(p):
            if "data" not in r or '"books"' not in r["data"][:40]:
                continue
            m = json.loads(r["data"])
            if "data" not in m:
                continue
            iid = m["arg"]["instId"]
            bids, asks = books[iid]
            if m.get("action") == "snapshot":
                bids.clear(), asks.clear()
            for side, book in (("bids", bids), ("asks", asks)):
                for px, sz, *_ in m["data"][0][side]:
                    if float(sz) == 0:
                        book.pop(float(px), None)
                    else:
                        book[float(px)] = float(sz)
            minute = r["ingested_at"] // 60_000_000_000
            if last_sample.get(iid) == minute or not bids or not asks:
                continue
            last_sample[iid] = minute
            bb, ba = max(bids), min(asks)
            if bb >= ba:
                continue
            mid = (bb + ba) / 2
            left, cost = notional, 0.0  # buy `notional` USD by walking the asks
            for px in sorted(asks):
                take = min(left, asks[px] * ct.get(iid, 1) * px)
                cost += take * (px / mid - 1)
                left -= take
                if left <= 0:
                    break
            rows.append({"inst": iid, "half_spread": (ba - bb) / 2 / mid, "impact": cost / notional if left <= 0 else None})
    df = pd.DataFrame(rows)
    out = df.groupby("inst").agg(samples=("half_spread", "size"), half_spread_bp=("half_spread", "median"), cost_bp=("impact", "median"))
    out[["half_spread_bp", "cost_bp"]] *= 1e4
    return out.sort_values("cost_bp")


if __name__ == "__main__":
    n = float(sys.argv[1]) if len(sys.argv) > 1 else 25_000
    print(f"market buy of {n:,.0f} USD, median over one-minute samples")
    print(main(n).round(1).to_string())
