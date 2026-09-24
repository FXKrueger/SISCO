"""H-001: reversals at Fibonacci retracement levels vs random levels. Rules in spec.yaml."""

import hashlib
import random
from datetime import timedelta

from harness.strategy import EntryRule, Signal

FIB = (0.382, 0.5, 0.618)


class Strategy:
    def __init__(self, params):
        self.levels = params["levels"]
        self.lookback = params["lookback_h"]

    def ratios(self, coin, t_low, t_high):
        if self.levels == "fib":
            return FIB
        seed = int(hashlib.sha256(f"{coin}{t_low}{t_high}".encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        return tuple(rng.uniform(0.3, 0.7) for _ in range(3))

    def on_bar(self, t, view):
        out = []
        for coin in view.universe():
            b = view.bars(coin, n=self.lookback)
            if len(b) < self.lookback:
                continue
            atr = (b.high - b.low).iloc[-24:].mean()
            hi, lo = b.high.max(), b.low.min()
            if atr <= 0 or (hi - lo) < 5 * atr:
                continue
            t_hi, t_lo = b.event_time[b.high.idxmax()], b.event_time[b.low.idxmin()]
            close = b.close.iloc[-1]
            up = t_lo < t_hi
            levels = [hi - r * (hi - lo) if up else lo + r * (hi - lo) for r in self.ratios(coin, t_lo, t_hi)]
            if up:
                below = [x for x in levels if close - 2 * atr <= x < close]
                if below:
                    lv = max(below)
                    out.append(Signal(coin, "long", EntryRule("limit", lv, timedelta(hours=4)), lv - atr, lv + 2.5 * atr,
                                      timedelta(hours=72), {"atr_pct": atr / close}))
            else:
                above = [x for x in levels if close < x <= close + 2 * atr]
                if above:
                    lv = min(above)
                    out.append(Signal(coin, "short", EntryRule("limit", lv, timedelta(hours=4)), lv + atr, lv - 2.5 * atr,
                                      timedelta(hours=72), {"atr_pct": atr / close}))
        return out
