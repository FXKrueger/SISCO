"""Binance USD-M futures history from data.binance.vision into the PIT store.

  python -m ingestion.binance_history [SYMBOL ...]     klines, funding, premium index: all symbols
  python -m ingestion.binance_history --metrics        OI and long/short metrics: coins ever in the top 30
  python -m ingestion.binance_history --holdout        THE LEAD ONLY: holdout months into data/holdout
                                                       (CLAUDE.md rule 2: agents never fetch or read it)

Writes data/store/binance_um/<kind>/<SYMBOL>.parquet. All USDT perps, including
delisted ones (survivorship). Only months before harness.config.DEV_END are downloaded:
the holdout is never fetched.
"""

import csv
import io
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from harness.config import DEV_END

BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
FILES = "https://data.binance.vision/"
STORE = Path(os.environ.get("SISCO_DATA", "data")) / "store" / "binance_um"
LAST_MONTH = f"{DEV_END.year}-{DEV_END.month - 1:02d}" if DEV_END.month > 1 else f"{DEV_END.year - 1}-12"

KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
FUNDING_COLS = ["calc_time", "funding_interval_hours", "funding_rate"]
# kind -> (path under data/futures/um/, csv columns or None when the file has a header)
KINDS = {
    "klines_1h": ("monthly/klines/{s}/1h/", KLINE_COLS),
    "funding": ("monthly/fundingRate/{s}/", FUNDING_COLS),
    "premium_1h": ("monthly/premiumIndexKlines/{s}/1h/", KLINE_COLS),
    "metrics": ("daily/metrics/{s}/", None),
}


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "SISCO/0.1"}), timeout=60) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2**i)


def list_prefix(prefix):
    """Yield (kind, value) for S3 CommonPrefixes ('dir') and Keys ('key'), following pagination."""
    marker = ""
    while True:
        xml = get(f"{BUCKET}?delimiter=/&prefix={urllib.parse.quote(prefix)}&marker={urllib.parse.quote(marker)}").decode()
        yield from (("dir", p) for p in re.findall(r"<Prefix>([^<]+/)</Prefix>", xml) if p != prefix)
        yield from (("key", k) for k in re.findall(r"<Key>([^<]+\.zip)</Key>", xml))
        if "<IsTruncated>true</IsTruncated>" not in xml:
            return
        marker = re.search(r"<NextMarker>([^<]+)</NextMarker>", xml).group(1)


def symbols():
    dirs = [v for k, v in list_prefix("data/futures/um/monthly/klines/") if k == "dir"]
    names = [d.rstrip("/").rsplit("/", 1)[1] for d in dirs]
    return [s for s in names if s.endswith("USDT") and "_" not in s]  # perps only, no dated futures


def read_zip(blob, cols):
    """Monthly CSVs have a header row since about 2022 and none before. cols None: use the header."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        rows = list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))))
    if cols is None:
        return pd.DataFrame(rows[1:], columns=rows[0])
    if rows and not rows[0][0].lstrip("-").isdigit():
        rows = rows[1:]
    return pd.DataFrame(rows, columns=cols[: len(rows[0])] if rows else cols)


def month_of(key):
    """YYYY-MM of a monthly or daily file, None for malformed names (Binance has a few)."""
    m = re.search(r"(\d{4}-\d{2})(-\d{2})?\.zip$", key)
    return m.group(1) if m else None


def fetch(symbol, kind, first_month="0000", last_month=LAST_MONTH, end=DEV_END):
    path, cols = KINDS[kind]
    keys = [v for k, v in list_prefix("data/futures/um/" + path.format(s=symbol)) if k == "key" and first_month <= (month_of(v) or "9999") <= last_month]
    if not keys:
        return None
    df = pd.concat([read_zip(get(FILES + urllib.parse.quote(k)), cols) for k in sorted(keys)])
    now = pd.Timestamp.now(tz="UTC")
    if kind == "metrics":
        df["event_time"] = pd.to_datetime(df.create_time, utc=True)
        df = df[df.event_time.dt.minute == 0].drop(columns=["create_time", "symbol"])  # hourly is enough
        for c in df.columns.drop("event_time"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        # When a snapshot becomes known is not documented. Assume 5 minutes late (CLAUDE.md rule 9).
        df["available_time"] = df.event_time + pd.Timedelta(minutes=5)
    elif kind in ("klines_1h", "premium_1h"):
        df = df.drop(columns=["ignore"], errors="ignore")
        for c in df.columns:
            df[c] = pd.to_numeric(df[c])
        df["event_time"] = pd.to_datetime(df.open_time, unit="ms", utc=True)
        df["available_time"] = pd.to_datetime(df.close_time + 1, unit="ms", utc=True)  # known at candle close
    else:
        df["calc_time"] = pd.to_numeric(df.calc_time)
        df["funding_rate"] = pd.to_numeric(df.funding_rate)
        df = df.drop(columns=["funding_interval_hours"])
        df["event_time"] = pd.to_datetime(df.calc_time, unit="ms", utc=True)
        df["available_time"] = df.event_time  # known at settlement
    df = df[df.available_time < pd.Timestamp(end)].drop_duplicates("event_time").sort_values("event_time")
    df["coin"] = symbol.removesuffix("USDT")
    df["ingested_at"], df["source"], df["revision"] = now, "binance_vision", 0
    return df


def download(symbol, kinds=("klines_1h", "funding", "premium_1h"), store=STORE, **window):
    try:
        _download(symbol, kinds, store, **window)
    except Exception as e:  # one broken symbol must not stop the rest; rerun retries it
        print(f"FAILED {symbol}: {e!r}", flush=True)
    return symbol


def _download(symbol, kinds, store=STORE, **window):
    for kind in kinds:
        out = store / kind / f"{symbol}.parquet"
        if out.exists():
            continue
        df = fetch(symbol, kind, **window)
        if df is not None and len(df):
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(out.with_suffix(".tmp"), index=False)
            out.with_suffix(".tmp").rename(out)


def top_symbols(n=30):
    """Symbols that are ever in the top n by liquidity, from the klines already in the store."""
    from harness.pit import liquidity

    vols = {p.stem: pd.read_parquet(p, columns=["available_time", "quote_volume"]) for p in (STORE / "klines_1h").glob("*.parquet")}
    universe, _ = liquidity(vols, n, 30)
    return sorted(set().union(*universe.values()))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--metrics"]:
        syms, job = top_symbols(), lambda s: download(s, ("metrics",))
    elif args == ["--holdout"]:
        from harness.config import HOLDOUT_END, HOLDOUT_START

        # Whole months inside the sealed window. Monthly files for the last partial month are not
        # published yet, so the holdout ends at the last full month.
        first = f"{HOLDOUT_START.year}-{HOLDOUT_START.month:02d}"
        last = f"{HOLDOUT_END.year}-{HOLDOUT_END.month - 1:02d}" if HOLDOUT_END.month > 1 else f"{HOLDOUT_END.year - 1}-12"
        store = STORE.parents[1] / "holdout" / "binance_um"
        syms, job = symbols(), lambda s: download(s, ("klines_1h", "funding"), store, first_month=first, last_month=last, end=HOLDOUT_END)
    else:
        syms, job = args or symbols(), download
    print(f"{len(syms)} symbols, months up to {LAST_MONTH}", flush=True)
    with ThreadPoolExecutor(16) as pool:
        for i, s in enumerate(pool.map(job, syms), 1):
            if i % 25 == 0:
                print(i, s, flush=True)
