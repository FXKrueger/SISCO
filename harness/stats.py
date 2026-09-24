"""Statistics (SPEC 6.2 #5, #7, #8). Daily series are in R per day; 1R = 0.5% of equity."""

from itertools import combinations
from math import sqrt
from statistics import NormalDist

import numpy as np
import pandas as pd

N = NormalDist()
EULER = 0.5772156649


def daily(trades, start, end):
    """Realized net R per UTC day (by exit day), zero on days without exits."""
    days = pd.date_range(pd.Timestamp(start).floor("D"), pd.Timestamp(end).floor("D"), freq="D")
    if not len(trades):
        return pd.Series(0.0, index=days)
    return trades.groupby(trades.exit_time.dt.floor("D")).net_R.sum().reindex(days, fill_value=0.0)


def sharpe(x, periods=365):
    x = np.asarray(x, float)
    sd = x.std(ddof=1)
    return 0.0 if sd == 0 or len(x) < 2 else float(x.mean() / sd * sqrt(periods))


def max_drawdown(x):
    eq = np.cumsum(x)
    return float((np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max()) if len(eq) else 0.0


def deflated_sharpe(x, trial_srs):
    """Deflated Sharpe Ratio (Bailey and Lopez de Prado 2014). x: this trial's daily series.
    trial_srs: per-day Sharpe of every trial of the hypothesis (including this one)."""
    x = np.asarray(x, float)
    T, sr = len(x), x.mean() / x.std(ddof=1) if x.std(ddof=1) > 0 else 0.0
    n = len(trial_srs)
    sr0 = 0.0
    if n > 1:
        v = np.var(trial_srs, ddof=1)
        sr0 = sqrt(v) * ((1 - EULER) * N.inv_cdf(1 - 1 / n) + EULER * N.inv_cdf(1 - 1 / (n * np.e)))
    z = pd.Series(x)
    skew, kurt = z.skew(), z.kurt() + 3
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if T < 2 or denom <= 0:
        return 0.0
    return N.cdf((sr - sr0) * sqrt(T - 1) / sqrt(denom))


def pbo(matrix, blocks=16):
    """Probability of Backtest Overfitting via CSCV. matrix: days x trials of daily returns."""
    m = np.asarray(matrix, float)
    if m.ndim != 2 or m.shape[1] < 2:
        return None
    parts = np.array_split(np.arange(len(m)), blocks)
    lam = []
    for is_idx in combinations(range(blocks), blocks // 2):
        ins = np.concatenate([parts[i] for i in is_idx])
        oos = np.concatenate([parts[i] for i in range(blocks) if i not in is_idx])
        s_in = m[ins].mean(0) / (m[ins].std(0, ddof=1) + 1e-12)
        s_out = m[oos].mean(0) / (m[oos].std(0, ddof=1) + 1e-12)
        best = np.argmax(s_in)
        w = (s_out < s_out[best]).sum() + 1
        w = w / (m.shape[1] + 1)
        lam.append(np.log(w / (1 - w)))
    return float(np.mean(np.array(lam) <= 0))


def null_test(trades, panel, runs=1000, seed=0, cost_mult=2.0, sessions=None):
    """Random entries run through the engine: for each real trade the same signal time, the same
    stop and target distances (in % of price), the same time limit, but a random coin from the
    universe at that time and a random side, entered at market. Payoffs are bounded by stop and
    target like the real trades, and costs come from the engine for that coin. Returns the 95th
    percentile of total net R over `runs` random portfolios and the share the strategy beat."""
    from .engine import last_close, simulate
    from .strategy import EntryRule, Signal

    if not len(trades):
        return None
    rng = np.random.default_rng(seed)
    options = []
    for tr in trades.itertuples():
        uni = panel._universe.get(tr.signal_time.floor("D")) or [tr.coin]
        sp = abs(tr.planned_entry - tr.stop) / tr.planned_entry
        tp = abs(tr.target - tr.planned_entry) / tr.planned_entry
        tl = pd.Timedelta(hours=tr.time_limit_h)
        net = []
        for c in uni:
            px = last_close(panel, c, tr.signal_time)
            if px is None:
                continue
            for s in (1, -1):
                sig = Signal(c, "long" if s == 1 else "short", EntryRule("market"), px * (1 - s * sp), px * (1 + s * tp), tl)
                r = simulate(tr.signal_time, sig, panel, cost_mult, sessions)
                if r is not None:
                    net.append(r["gross_R"] + r["cost_R"] + r["funding_R"])
        options.append(np.array(net or [0.0]))
    totals = np.zeros(runs)
    for net in options:
        totals += net[rng.integers(len(net), size=runs)]
    real = trades.net_R.sum()
    return {"p95": float(np.percentile(totals, 95)), "beats_share": float((real > totals).mean()), "passed": bool(real > np.percentile(totals, 95))}


def by_window(trades, windows):
    out = {}
    for name, (a, b) in windows.items():
        m = (trades.entry_time >= pd.Timestamp(a, tz="UTC")) & (trades.entry_time < pd.Timestamp(b, tz="UTC")) if len(trades) else []
        t = trades[m] if len(trades) else trades
        out[name] = {"trades": int(len(t)), "net_R": float(t.net_R.sum()) if len(t) else 0.0,
                     "win_rate": float((t.net_R > 0).mean()) if len(t) else None}
    return out
