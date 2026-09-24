"""Backtest engine. The harness owns fills, costs, funding and labels; strategies only propose.

Timing (SPEC 6.2 #1): a signal made at t (a bar close) fills at the earliest in the bar that
starts at t. Market orders fill at that bar's open. Limit orders fill when price trades through
the limit before expiry. Everything is measured in R: 1R = the distance from the planned entry to the stop.

Session mode (D19): with sessions set, time-limit exits and limit-order expiries happen at the
first session at or after the limit, because nothing runs between sessions. delay_h shifts the
earliest fill by that many hours (reaction-delay test).

Conservative intrabar rules (bars have no tick order):
- stop and target touched in the same bar: the stop wins,
- a limit filled inside a bar can be stopped in that bar (any extreme beyond the limit comes
  after the first touch), and reaches its target in that bar only if the bar closes beyond it,
- a stop gapped through fills at the bar open, plus stop slippage.
"""

import numpy as np
import pandas as pd

from . import costs
from .pit import PITView


class SignalError(Exception):
    """A strategy produced an impossible signal (stop or target on the wrong side)."""


def next_session(ts, sessions):
    """First session time (UTC hour in sessions) at or after ts. sessions None: ts itself."""
    if not sessions:
        return ts
    ts = pd.Timestamp(ts).ceil("h")
    while ts.hour not in sessions:
        ts += pd.Timedelta(hours=1)
    return ts


def generate(strategy, panel, times):
    out = []
    for t in times:
        for s in strategy.on_bar(t, PITView(panel, t)) or []:
            out.append((t, s))
    return out


def simulate(t, sig, panel, cost_mult=1.0, sessions=None, delay_h=0):
    """One signal against the bars after t. Returns a trade dict, or None if a limit never filled."""
    d = panel.bars.get(sig.coin)
    if d is None:
        return None
    side = 1 if sig.side == "long" else -1
    cache = panel.__dict__.setdefault("_arrays", {})
    if sig.coin not in cache:
        cache[sig.coin] = (d.event_time.to_numpy("datetime64[ns]"), *(d[k].to_numpy(float) for k in ("open", "high", "low", "close")))
    et, o, h, l, c = cache[sig.coin]
    i0 = np.searchsorted(et, np.datetime64(t.tz_convert(None), "ns"))
    i = np.searchsorted(et, np.datetime64((t + pd.Timedelta(hours=delay_h)).tz_convert(None), "ns"))
    if i >= len(d):
        return None
    ref = sig.entry.price if sig.entry.kind == "limit" else (c[i0 - 1] if i0 else o[i0])
    if not (side * (ref - sig.stop) > 0 and side * (sig.target - ref) > 0):
        raise SignalError(f"{sig} at {t}: stop and target must be on opposite sides of entry {ref}")
    if delay_h and sig.entry.kind == "market" and side * (o[i] - sig.stop) <= 0:
        return None  # reacting late: nobody enters a trade whose stop is already through

    # Entry
    intrabar_fill = False
    if sig.entry.kind == "market":
        j, entry = i, o[i]
    else:
        expiry = next_session(t + pd.Timedelta(hours=delay_h) + sig.entry.expiry, sessions)
        expiry = np.datetime64(expiry.tz_convert(None), "ns")
        j, entry = i, None
        while j < len(d) and et[j] < expiry:
            if side * (sig.entry.price - o[j]) >= 0:  # opened through the limit: fill at the better open
                entry = o[j]
                break
            if (l[j] if side == 1 else -h[j]) <= side * sig.entry.price:
                entry, intrabar_fill = sig.entry.price, True
                break
            j += 1
        if entry is None:
            return None
    # 1R is fixed when the order is sent: planned entry (limit price, or last close for market
    # orders) to stop. A better or worse fill changes the result in R, not the size of R.
    risk = abs(ref - sig.stop)

    # Exit
    deadline = next_session(pd.Timestamp(et[j], tz="UTC") + sig.time_limit, sessions)
    deadline = np.datetime64(deadline.tz_convert(None), "ns")
    k, reason, exit_px = j, None, None
    while k < len(d):
        hit_stop = (l[k] <= sig.stop) if side == 1 else (h[k] >= sig.stop)
        hit_tgt = (h[k] >= sig.target) if side == 1 else (l[k] <= sig.target)
        if hit_stop:
            gapped = side * (o[k] - sig.stop) <= 0 and not (k == j and intrabar_fill)
            reason, exit_px = "stop", o[k] if gapped else sig.stop
            break
        if k == j and intrabar_fill:
            # The high (long) may predate the fill. The close does not: a close beyond the target
            # means price crossed the target after the fill.
            hit_tgt = side * (c[k] - sig.target) >= 0
        if hit_tgt:
            reason, exit_px = "target", sig.target
            break
        if et[k] + np.timedelta64(1, "h") >= deadline:
            reason, exit_px = "time", c[k]
            break
        k += 1
    if reason is None:
        k, reason, exit_px = len(d) - 1, "end", c[-1]

    entry_time = pd.Timestamp(et[j], tz="UTC")
    exit_time = pd.Timestamp(et[k], tz="UTC") + pd.Timedelta(hours=1)
    stop_pct = risk / ref
    cost = costs.entry_cost(sig.entry.kind, panel.adv(sig.coin, t)) + costs.exit_cost(
        reason, panel.adv(sig.coin, exit_time), (h[k] - l[k]) / o[k])
    fcache = panel.__dict__.setdefault("_farrays", {})
    if sig.coin not in fcache:
        f = panel.funding.get(sig.coin)
        fcache[sig.coin] = (f.event_time.to_numpy("datetime64[ns]"), f.funding_rate.to_numpy(float)) if f is not None and len(f) else None
    fund = 0.0
    if fcache[sig.coin] is not None:
        fe, fr = fcache[sig.coin]
        # Stops and targets fill inside the bar, before a settlement at its end. Time exits fill at
        # the close, which coincides with a settlement: count it (conservative).
        a = np.searchsorted(fe, np.datetime64(entry_time.tz_convert(None), "ns"), side="right")
        b = np.searchsorted(fe, np.datetime64(exit_time.tz_convert(None), "ns"), side="right" if reason in ("time", "end") else "left")
        fund = -side * fr[a:b].sum()  # positive rate: longs pay shorts
    return {
        "coin": sig.coin, "side": sig.side, "signal_time": t, "entry_kind": sig.entry.kind,
        "entry_time": entry_time, "planned_entry": ref, "entry": entry, "stop": sig.stop, "target": sig.target,
        "exit_time": exit_time, "exit": exit_px, "reason": reason, "time_limit_h": sig.time_limit / pd.Timedelta(hours=1),
        "gross_R": side * (exit_px - entry) / risk,
        "cost_R": -cost_mult * cost / stop_pct,
        "funding_R": fund / stop_pct,
        **{f"f_{k}": v for k, v in sig.features.items()},
    }


def last_close(panel, coin, t):
    """Close of the last bar known at t, or None."""
    d = panel.bars.get(coin)
    if d is None:
        return None
    i = np.searchsorted(panel._avail[coin], np.datetime64(pd.Timestamp(t).tz_convert(None), "ns"), side="right")
    return float(d.close.iat[i - 1]) if i else None


def beta(panel, coin, t, days=30):
    """Beta of hourly returns to BTC over the `days` days before the day of t (known at t).
    Same rule as the live risk engine (ops/live_data.beta_to_btc). Cached per coin and day."""
    if coin == "BTC" or "BTC" not in panel.bars:
        return 1.0
    cache = panel.__dict__.setdefault("_betas", {})
    if coin not in cache:
        r = {c: np.log(panel.bars[c].set_index("available_time").close).diff() for c in (coin, "BTC")}
        x = pd.concat(r, axis=1).dropna()
        cov = x[coin].rolling(24 * days, min_periods=48).cov(x["BTC"])
        b = (cov / x["BTC"].rolling(24 * days, min_periods=48).var()).groupby(x.index.floor("D")).last().shift(1)
        cache[coin] = b.fillna(1.0)
    b = cache[coin]
    return float(b.asof(pd.Timestamp(t).floor("D"))) if len(b) and pd.Timestamp(t).floor("D") >= b.index[0] else 1.0


def backtest(signals, panel, cost_mult=1.0, max_open=5, sessions=None, delay_h=0, max_same_direction=3.0):
    """Simulate signals in time order with the live risk rules (SPEC 10, risk/engine.py): one
    position per coin, at most max_open 1R positions, BTC-beta adjusted exposure at most
    max_same_direction R per direction. Pending limit orders count as open until they fill or expire."""
    trades, busy, skipped = [], {}, 0
    for t, sig in sorted(signals, key=lambda x: x[0]):
        busy = {c: v for c, v in busy.items() if v[0] > t}
        s = 1 if sig.side == "long" else -1
        b = beta(panel, sig.coin, t) if max_same_direction else 1.0
        exposure = sum(v[1] for v in busy.values()) + s * b
        if sig.coin in busy or len(busy) >= max_open or (max_same_direction and abs(exposure) > max_same_direction + 1e-9):
            skipped += 1
            continue
        tr = simulate(t, sig, panel, cost_mult, sessions, delay_h)
        if tr is None:
            busy[sig.coin] = (next_session(t + pd.Timedelta(hours=delay_h) + sig.entry.expiry, sessions), s * b)
            continue
        busy[sig.coin] = (tr["exit_time"], s * b)
        trades.append(tr)
    df = pd.DataFrame(trades)
    if len(df):
        df["net_R"] = df.gross_R + df.cost_R + df.funding_R
    df.attrs["skipped_signals"] = skipped
    return df
