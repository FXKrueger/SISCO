"""B-001: trend baseline. Rules in spec.yaml."""

from datetime import timedelta

from harness.strategy import EntryRule, Signal

LOOKBACKS_D = (7, 30, 90)


class Strategy:
    def __init__(self, params):
        pass

    def on_bar(self, t, view):
        out = []
        need = 24 * max(LOOKBACKS_D) + 1
        for coin in view.universe():
            b = view.bars(coin, n=need)
            if len(b) < need:
                continue
            px = b.close.iloc[-1]
            signs = {1 if px > b.close.iloc[-1 - 24 * d] else -1 for d in LOOKBACKS_D}
            if len(signs) != 1:
                continue
            s = signs.pop()
            atr = (b.high - b.low).iloc[-24 * 14 :].mean() * 24**0.5
            if atr <= 0:
                continue
            out.append(Signal(coin, "long" if s == 1 else "short", EntryRule("market"), px - s * 2 * atr, px + s * 6 * atr,
                              timedelta(days=7), {"atr_pct": atr / px}))
        return out
