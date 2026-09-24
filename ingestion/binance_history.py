"""Binance USD-M futures history from data.binance.vision into the PIT store.

  python -m ingestion.binance_history [SYMBOL ...]

Writes data/store/binance_um/{klines_1h,funding}/<SYMBOL>.parquet. All USDT perps, including
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
    """Monthly CSVs have a header row since about 2022 and none before."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        rows = list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))))
    if rows and not rows[0][0].lstrip("-").isdigit():
        rows = rows[1:]
    return pd.DataFrame(rows, columns=cols[: len(rows[0])] if rows else cols)


def month_of(key):
    return key.rsplit("-", 2)[-2] + "-" + key.rsplit("-", 1)[-1][:2]


def fetch(symbol, kind):
    prefix = f"data/futures/um/monthly/{'klines' if kind == 'klines_1h' else 'fundingRate'}/{symbol}/" + ("1h/" if kind == "klines_1h" else "")
    keys = [v for k, v in list_prefix(prefix) if k == "key" and month_of(v) <= LAST_MONTH]
    if not keys:
        return None
    df = pd.concat([read_zip(get(FILES + urllib.parse.quote(k)), KLINE_COLS if kind == "klines_1h" else FUNDING_COLS) for k in sorted(keys)])
    now = pd.Timestamp.now(tz="UTC")
    if kind == "klines_1h":
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
    df = df[df.available_time < pd.Timestamp(DEV_END)].drop_duplicates("event_time").sort_values("event_time")
    df["coin"] = symbol.removesuffix("USDT")
    df["ingested_at"], df["source"], df["revision"] = now, "binance_vision", 0
    return df


def download(symbol):
    try:
        _download(symbol)
    except Exception as e:  # one broken symbol must not stop the rest; rerun retries it
        print(f"FAILED {symbol}: {e!r}", flush=True)
    return symbol


def _download(symbol):
    for kind in ("klines_1h", "funding"):
        out = STORE / kind / f"{symbol}.parquet"
        if out.exists():
            continue
        df = fetch(symbol, kind)
        if df is not None and len(df):
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(out.with_suffix(".tmp"), index=False)
            out.with_suffix(".tmp").rename(out)


if __name__ == "__main__":
    syms = sys.argv[1:] or symbols()
    print(f"{len(syms)} symbols, months up to {LAST_MONTH}", flush=True)
    with ThreadPoolExecutor(16) as pool:
        for i, s in enumerate(pool.map(download, syms), 1):
            if i % 25 == 0:
                print(i, s, flush=True)
