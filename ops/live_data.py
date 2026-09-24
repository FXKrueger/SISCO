"""Live data for sessions: the same sources and schema as the research store (Binance USD-M
hourly bars and funding), so live signals come from the same code and data as the backtest.

Only closed bars are included (available_time <= now). The universe rule is the harness rule,
applied to the most liquid 60 perps by 24h volume.
"""

import json
import urllib.request
from pathlib import Path

import pandas as pd

from harness.pit import Panel

FAPI = "https://fapi.binance.com"
# Symbols with development history (config/research_symbols.txt, from the research store). Live
# trading only considers these: strategies were tested on them, not on perps listed later
# (stock and commodity perps, new coins).
RESEARCH = set(Path(__file__).parents[1].joinpath("config", "research_symbols.txt").read_text().split())


def _get(path):
    with urllib.request.urlopen(urllib.request.Request(FAPI + path, headers={"User-Agent": "sisco"}), timeout=30) as r:
        return json.loads(r.read())


def candidates(n=60):
    rows = [t for t in _get("/fapi/v1/ticker/24hr") if t["symbol"] in RESEARCH]
    return [t["symbol"] for t in sorted(rows, key=lambda t: -float(t["quoteVolume"]))[:n]]


def klines(symbol, days, now):
    end = int(now.timestamp() * 1000)
    start = end - days * 86_400_000
    rows = []
    while start < end:
        chunk = _get(f"/fapi/v1/klines?symbol={symbol}&interval=1h&startTime={start}&limit=1000")
        if not chunk:
            break
        rows += chunk
        start = chunk[-1][0] + 3_600_000
        if len(chunk) < 1000:
            break
    d = pd.DataFrame([r[:11] for r in rows], columns=["open_time", "open", "high", "low", "close", "volume", "close_time",
                                                     "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume"]).astype(float)
    d["event_time"] = pd.to_datetime(d.open_time, unit="ms", utc=True)
    d["available_time"] = pd.to_datetime(d.close_time + 1, unit="ms", utc=True)
    d = d[d.available_time <= now].drop_duplicates("event_time")  # closed bars only
    d["coin"] = symbol.removesuffix("USDT")
    return d


def funding(symbol, now):
    rows = _get(f"/fapi/v1/fundingRate?symbol={symbol}&limit=1000")
    d = pd.DataFrame({"event_time": pd.to_datetime([r["fundingTime"] for r in rows], unit="ms", utc=True),
                      "funding_rate": [float(r["fundingRate"]) for r in rows]})
    d["available_time"] = d.event_time
    return d[d.available_time <= now]


def panel(now, top_n, days=120):
    """A harness Panel over the last `days` days, known at `now`."""
    bars, fund = {}, {}
    for sym in candidates():
        k = klines(sym, days, now)
        if len(k):
            coin = sym.removesuffix("USDT")
            bars[coin], fund[coin] = k, funding(sym, now)
    return Panel(bars, fund, top_n=top_n)  # events: the cached LLM events, cut at `now` by the PITView


def base_coin(coin):
    """Binance multiplier symbols (1000PEPE) to the plain coin (PEPE) used by X-Perps."""
    return coin.lstrip("0123456789") if coin[:1].isdigit() else coin
