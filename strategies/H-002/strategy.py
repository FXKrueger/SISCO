"""H-002: time-series momentum on the most liquid coins. Rules in spec.yaml."""

from datetime import timedelta

from harness.strategy import EntryRule, Signal


class Strategy:
    def __init__(self, params):
        self.coins = params["coins"]
        self.lookback = params["lookback_d"]

    def on_bar(self, t, view):
        out = []
        need = 24 * self.lookback + 1
        for coin in view.universe()[: self.coins]:
            b = view.bars(coin, n=need)
            if len(b) < need:
                continue
            px = b.close.iloc[-1]
            s = 1 if px > b.close.iloc[0] else -1
            atr = (b.high - b.low).iloc[-24 * 14 :].mean() * 24**0.5
            if atr <= 0:
                continue
            out.append(Signal(coin, "long" if s == 1 else "short", EntryRule("market"), px - s * 3 * atr, px + s * 20 * atr,
                              timedelta(days=7), {"atr_pct": atr / px, "lookback_ret": px / b.close.iloc[0] - 1}))
        return out
