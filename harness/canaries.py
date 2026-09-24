"""Harness self-tests (SPEC 6.3). Run on every harness change and weekly.

  python -m harness.canaries          all three (known-effect needs the Binance store)

Canary runs are self-tests, not trials: they do not touch the registry.
"""

from datetime import timedelta

import numpy as np
import pandas as pd

from . import engine, stats
from .pit import LookaheadError, Panel
from .run import Refused, lint
from .strategy import EntryRule, Signal


def synthetic_panel(coins=5, days=400, drift=0.0, vol=0.01, seed=1):
    """Hourly bars aggregated from a minute-level random walk, so high and low are true path extremes
    and there is nothing to predict."""
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp("2021-01-01", tz="UTC")
    et = pd.date_range(t0, periods=days * 24, freq="h")
    bars, fund = {}, {}
    for i in range(coins):
        path = 100 * np.exp(np.cumsum(rng.normal(drift / 60, vol / 60**0.5, len(et) * 60)))
        m = np.concatenate([[100], path]).reshape(-1)[:-1].reshape(len(et), 60)  # minute prices, hour by hour
        close = np.concatenate([m[1:, 0], [path[-1]]])
        open_ = m[:, 0]
        high = np.maximum(m.max(1), close)
        low = np.minimum(m.min(1), close)
        c = f"C{i}"
        bars[c] = pd.DataFrame({"event_time": et, "available_time": et + pd.Timedelta(hours=1), "open": open_, "high": high,
                                "low": low, "close": close, "quote_volume": 1e9 / 24, "coin": c})
        ft = pd.date_range(t0, et[-1], freq="8h")
        fund[c] = pd.DataFrame({"event_time": ft, "available_time": ft, "funding_rate": 0.0})
    return Panel(bars, fund, top_n=coins)


class Peek:
    """Reads one bar ahead. The harness must refuse."""

    def __init__(self, params=None):
        pass

    def on_bar(self, t, view):
        view.bars("C0", end=t + timedelta(hours=1))
        return []


class RandomEntries:
    def __init__(self, params=None):
        self.rng = np.random.default_rng((params or {}).get("seed", 0))

    def on_bar(self, t, view):
        if self.rng.random() > 0.3:
            return []
        coin = self.rng.choice(view.universe() or ["C0"])
        last = view.bars(coin, n=1)
        if not len(last):
            return []
        px, side = last.close.iloc[-1], self.rng.choice(["long", "short"])
        s = 1 if side == "long" else -1
        return [Signal(coin, side, EntryRule("market"), px * (1 - s * 0.03), px * (1 + s * 0.06), timedelta(hours=48))]


class Trend:
    """Time-series momentum on BTC: sign of the 30-day return, re-decided daily, held up to 7 days."""

    def __init__(self, params=None):
        pass

    def on_bar(self, t, view):
        b = view.bars("BTC", n=24 * 30 + 1)
        if len(b) < 24 * 30 + 1:
            return []
        px = b.close.iloc[-1]
        atr = (b.high - b.low).iloc[-24 * 14 :].mean() * 24**0.5  # rough daily range
        s = 1 if px > b.close.iloc[0] else -1
        return [Signal("BTC", "long" if s == 1 else "short", EntryRule("market"), px - s * 3 * atr, px + s * 20 * atr, timedelta(days=7))]


def leak_canary():
    try:
        engine.generate(Peek(), synthetic_panel(coins=1, days=40))
    except LookaheadError:
        pass
    else:
        raise AssertionError("leak canary: a read one bar ahead was not refused")
    try:
        lint("def on_bar(self, t, view):\n    return view._PITView__panel.bars")
    except Refused:
        return "pass"
    raise AssertionError("leak canary: private access to the panel was not refused")


def random_canary():
    panel = synthetic_panel()
    tr = engine.backtest(engine.generate(RandomEntries(), panel, step_hours=6), panel)
    se = tr.gross_R.std(ddof=1) / len(tr) ** 0.5
    assert len(tr) > 200, f"random canary: too few trades ({len(tr)})"
    assert abs(tr.gross_R.mean()) < 3 * se, f"random canary: gross mean {tr.gross_R.mean():.3f} R is not ~0 (se {se:.3f})"
    assert abs(tr.net_R.mean() - (tr.gross_R.mean() + tr.cost_R.mean())) < 1e-9 and tr.cost_R.mean() < 0
    return f"pass: {len(tr)} trades, gross {tr.gross_R.mean():+.3f} R/trade (se {se:.3f}), costs {tr.cost_R.mean():+.3f} R/trade"


def known_effect_canary():
    panel = Panel.load(coins={"BTC"}, top_n=1)
    tr = engine.backtest(engine.generate(Trend(), panel, step_hours=24), panel)
    d = stats.daily(tr.assign(net_R=tr.gross_R), panel.timeline()[0], panel.timeline()[-1])
    sr = stats.sharpe(d)
    assert sr > 0, f"known-effect canary: BTC trend gross Sharpe {sr:.2f} <= 0, published results say positive"
    return f"pass: BTC trend gross Sharpe {sr:.2f} over {len(tr)} trades"


if __name__ == "__main__":
    for c in (leak_canary, random_canary, known_effect_canary):
        print(c.__name__, c())
