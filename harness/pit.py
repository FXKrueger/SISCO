"""Point-in-time data access. The only way strategy code sees data (CLAUDE.md rule 5)."""

import os
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DEV_END

STORE = Path(os.environ.get("SISCO_DATA", "data")) / "store" / "binance_um"
EVENTS = Path(os.environ.get("SISCO_DATA", "data")) / "llm" / "events.parquet"


def load_events(path=EVENTS):
    """Valid LLM events (SPEC 8.2) from the cached extraction pipeline. Empty if none yet."""
    if not Path(path).exists():
        return pd.DataFrame(columns=["available_time", "event_type", "coins", "direction", "surprise", "credibility"])
    e = pd.read_parquet(path)
    return e[e.valid].sort_values("available_time").reset_index(drop=True)
BAR = timedelta(hours=1)


class LookaheadError(Exception):
    pass


def liquidity(volumes, top_n, min_days):
    """Universe on day D: top_n coins by 30-day quote volume over days < D (all known at D 00:00),
    with at least min_days of history. Delisted coins drop out when their volume stops.
    Also returns average daily quote volume per coin and day, for the cost model."""
    daily = pd.DataFrame(
        {c: d.set_index(d.available_time.dt.floor("D")).quote_volume.groupby(level=0).sum() for c, d in volumes.items()}
    ).sort_index().asfreq("D")
    # A missing day (exchange outage) must not drop a coin for a month: need 2/3 of the window.
    vol30 = (daily.rolling(30, min_periods=min(min_days, 20)).mean() * 30).shift(1)
    vol30 = vol30.where(daily.notna().cumsum().shift(1) >= min_days)
    return {day: list(row.dropna().nlargest(top_n).index) for day, row in vol30.iterrows()}, vol30 / 30


class Panel:
    """All bars and funding rows for a set of coins, plus a point-in-time liquidity universe."""

    def __init__(self, bars, funding, top_n=30, min_days=30, volumes=None, events=None):
        """volumes: {coin: frame with available_time, quote_volume} for the universe ranking.
        Defaults to bars. Pass all coins here even when bars holds only the ones that matter."""
        self.bars = {c: d.sort_values("available_time").reset_index(drop=True) for c, d in bars.items()}
        self.funding = {c: d.sort_values("available_time").reset_index(drop=True) for c, d in funding.items()}
        self._avail = {c: d.available_time.to_numpy("datetime64[ns]") for c, d in self.bars.items()}
        self._favail = {c: d.available_time.to_numpy("datetime64[ns]") for c, d in self.funding.items()}
        self._universe, self._adv = liquidity(volumes or self.bars, top_n, min_days)
        self.events = events if events is not None else load_events()
        self._eavail = self.events.available_time.to_numpy("datetime64[ns]") if len(self.events) else np.array([], "datetime64[ns]")

    def adv(self, coin, t):
        try:
            v = self._adv.at[pd.Timestamp(t).floor("D"), coin]
        except KeyError:
            return 0.0
        return 0.0 if pd.isna(v) else float(v)

    @classmethod
    def load(cls, coins=None, top_n=30, start=None, stores=(STORE,), end=DEV_END):
        """Rank all coins by liquidity, then load full bars only for coins that ever make the universe.
        Default: the development store, cut at DEV_END. Only the gatekeeper passes the holdout store."""
        end = pd.Timestamp(end)

        def read(kind, name, cols=None):
            parts = [pd.read_parquet(s / kind / name, columns=cols) for s in stores if (s / kind / name).exists()]
            if not parts:
                return None
            d = pd.concat(parts).drop_duplicates("available_time" if cols else "event_time")
            return d[d.available_time < end].sort_values("available_time")

        names = sorted({p.name for s in stores for p in (s / "klines_1h").glob("*.parquet")})
        vols = {n.removesuffix("USDT.parquet"): read("klines_1h", n, ["available_time", "quote_volume"]) for n in names}
        universe, _ = liquidity(vols, top_n, 30)
        keep = set(coins) if coins else set().union(*universe.values())
        kl, fu = {}, {}
        for n in names:
            coin = n.removesuffix("USDT.parquet")
            if coin not in keep:
                continue
            d = read("klines_1h", n)
            d = d[d.available_time >= pd.Timestamp(start or "1970-01-01", tz="UTC")]
            if len(d):
                kl[coin] = d
            f = read("funding", n)
            if f is not None:
                fu[coin] = f
        return cls(kl, fu, top_n=top_n, volumes=vols)

    def sessions(self, hours):
        """Decision times in session mode (D19): every day at the given UTC hours."""
        t = self.timeline()
        return t[t.hour.isin(hours)]

    def timeline(self, step_hours=1):
        """Decision times: every bar close (hourly), from the first bar to the end of development data."""
        start = min(d.available_time.iloc[0] for d in self.bars.values())
        end = max(d.available_time.iloc[-1] for d in self.bars.values())
        return pd.date_range(start.ceil("h"), end, freq=f"{step_hours}h")


class PITView:
    """What was known at time t. Every read returns only rows with available_time <= t."""

    def __init__(self, panel, t):
        self.__panel = panel
        self.__t = np.datetime64(pd.Timestamp(t).tz_convert(None), "ns")
        self.t = t

    def _cut(self, avail, n, end):
        cut = self.__t
        if end is not None:
            cut = np.datetime64(pd.Timestamp(end).tz_convert(None), "ns")
            if cut > self.__t:
                raise LookaheadError(f"asked for data up to {end}, view is at {self.t}")
        hi = np.searchsorted(avail, cut, side="right")
        return max(0, hi - n) if n else 0, hi

    def bars(self, coin, n=None, end=None):
        """Hourly bars for coin, oldest first. n: only the last n bars. end: known by then (<= t)."""
        if coin not in self.__panel.bars:
            return self.__panel.bars[next(iter(self.__panel.bars))].iloc[0:0].copy()
        lo, hi = self._cut(self.__panel._avail[coin], n, end)
        return self.__panel.bars[coin].iloc[lo:hi].copy()

    def funding(self, coin, n=None, end=None):
        if coin not in self.__panel.funding:
            return pd.DataFrame(columns=["event_time", "available_time", "funding_rate"])
        lo, hi = self._cut(self.__panel._favail[coin], n, end)
        return self.__panel.funding[coin].iloc[lo:hi].copy()

    def events(self, n=None, end=None):
        """LLM events known at t (available_time = when the extraction finished), oldest first.
        Columns: event_type, coins (JSON list), direction, surprise, credibility, time_sensitivity_h."""
        lo, hi = self._cut(self.__panel._eavail, n, end)
        return self.__panel.events.iloc[lo:hi].copy()

    def universe(self):
        """Coins tradeable at t by the liquidity rule, computed from data known at t."""
        return list(self.__panel._universe.get(pd.Timestamp(self.t).floor("D"), []))
