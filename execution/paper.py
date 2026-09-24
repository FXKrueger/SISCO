"""Paper broker: shadow fills on live X-Perps prices, no keys, no real orders (SPEC 15: paper
trading uses shadow fills on the live X-Perps order book). Protected.

Same interface as OkxBroker. State lives in the journal (trades with mode = 'paper').
Fill rules match the backtest engine: market entries fill at the live ask (buy) or bid (sell);
limits fill when a 1h candle trades through them; stop and target in the same candle: the stop
wins; a gap through the stop fills at the candle open. Exits pay the taker fee plus half the
live spread; funding is the real X-Perps funding.
"""

import time

from harness import costs

from . import okx


def _utc_ms(iso):
    import pandas as pd

    return int(pd.Timestamp(iso).timestamp() * 1000)


class PaperBroker:
    mode = "paper"

    def __init__(self, journal):
        self.j = journal
        self.errors = 0
        self.instruments = okx.xperp_instruments()

    def setup(self):
        pass

    def _open(self):
        return self.j.trades("mode = 'paper' AND status IN ('pending', 'open')")

    def _mark(self, t):
        bid, ask, last = okx.ticker(t["inst_id"])
        s = 1 if t["side"] == "long" else -1
        return s * (last - t["entry_px"]) * t["contracts"] * t["ct_val"]

    def account(self):
        closed = sum(self.j.r_multiple(t) * t["one_r_usd"] for t in self.j.trades("mode = 'paper' AND status = 'closed'"))
        live = self._open()
        upl = sum(self._mark(t) for t in live if t["status"] == "open")
        equity = self.j.get("paper_equity_start", 0) + closed + upl
        margin = sum(t["contracts"] * t["ct_val"] * (t["entry_px"] or t["planned_entry"]) / t["leverage"] for t in live)
        return {"equity": equity, "available": equity - margin}

    def positions(self):
        return [{"instId": t["inst_id"], "side": t["side"], "contracts": t["contracts"], "avg_px": t["entry_px"], "upl": self._mark(t)}
                for t in self._open() if t["status"] == "open"]

    def pending(self):
        return [{"instId": t["inst_id"], "clOrdId": t["id"], "ordId": t["id"]} for t in self._open() if t["status"] == "pending"]

    def protected(self, inst_id):
        return True  # stops are part of the simulation

    def place_entry(self, inst_id, side, contracts, kind, px, stop, target, leverage, cl_ord_id):
        if kind == "market":
            bid, ask, _ = okx.ticker(inst_id)
            fill = ask if side == "long" else bid
            now = int(time.time() * 1000)
            self.j.update(cl_ord_id, entry_px=fill, filled_at=_iso(now), status="open", checked_to_ms=now,
                          fee_usd=costs.TAKER_FEE * contracts * self._ct(inst_id) * fill)
        else:
            self.j.update(cl_ord_id, status="pending", checked_to_ms=int(time.time() * 1000))
        return cl_ord_id

    def cancel(self, inst_id, cl_ord_id):
        self.j.update(cl_ord_id, status="cancelled", closed_at=_iso(int(time.time() * 1000)))

    def close(self, inst_id, reason="time"):
        for t in self._open():
            if t["inst_id"] == inst_id and t["status"] == "open":
                bid, ask, _ = okx.ticker(inst_id)
                self._exit(t, bid if t["side"] == "long" else ask, int(time.time() * 1000), reason, spread=0.0)

    def _ct(self, inst_id):
        return next(v["ct_val"] for v in self.instruments.values() if v["instId"] == inst_id)

    def _exit(self, t, px, ms, reason, spread):
        s = 1 if t["side"] == "long" else -1
        px = px - s * spread / 2  # market exit crosses half the spread
        notional = t["contracts"] * t["ct_val"] * px
        funding = sum(-s * rate * notional for ts, rate in okx.funding_history(t["inst_id"], _utc_ms(t["filled_at"])) if ts <= ms)
        self.j.update(t["id"], exit_px=px, exit_reason=reason, closed_at=_iso(ms), status="closed",
                      pnl_usd=s * (px - t["entry_px"]) * t["contracts"] * t["ct_val"],
                      fee_usd=(t["fee_usd"] or 0) + costs.TAKER_FEE * notional, funding_usd=funding)

    def sync(self):
        """Advance every paper order and position through the 1h candles closed since the last check."""
        for t in self._open():
            bars = _bars_since(t["inst_id"], t["checked_to_ms"])
            if not bars:
                continue
            bid, ask, _ = okx.ticker(t["inst_id"])
            spread = ask - bid
            s = 1 if t["side"] == "long" else -1
            status, entry, intrabar = t["status"], t["entry_px"], False
            for ts, o, h, l, c in bars:
                if status == "pending":
                    if s * (t["planned_entry"] - o) >= 0:
                        entry = o
                    elif (l if s == 1 else -h) <= s * t["planned_entry"]:
                        entry, intrabar = t["planned_entry"], True
                    else:
                        continue
                    status = "open"
                    self.j.update(t["id"], entry_px=entry, filled_at=_iso(ts), status="open",
                                  fee_usd=costs.MAKER_FEE * t["contracts"] * t["ct_val"] * entry)
                    t = {**t, "entry_px": entry, "filled_at": _iso(ts), "status": "open", "fee_usd": costs.MAKER_FEE * t["contracts"] * t["ct_val"] * entry}
                hit_stop = (l <= t["stop"]) if s == 1 else (h >= t["stop"])
                hit_tgt = (s * (c - t["target"]) >= 0) if intrabar else ((h >= t["target"]) if s == 1 else (l <= t["target"]))
                intrabar = False
                if hit_stop:
                    self._exit(t, o if s * (o - t["stop"]) <= 0 else t["stop"], ts + 3_600_000, "stop", spread)
                    break
                if hit_tgt:
                    self._exit(t, t["target"], ts + 3_600_000, "target", spread)
                    break
            else:
                self.j.update(t["id"], checked_to_ms=bars[-1][0] + 3_600_000)


def _iso(ms):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="seconds")


def _bars_since(inst_id, since_ms):
    """Confirmed 1h candles starting at or after the hour containing since_ms, oldest first."""
    start = since_ms - since_ms % 3_600_000
    out, after = [], None
    for _ in range(10):
        rows = okx.candles(inst_id, after_ms=after, limit=100)
        if not rows:
            break
        out += [r for r in rows if r[0] >= start and r[5]]
        if rows[-1][0] <= start:
            break
        after = rows[-1][0]
    return sorted({r[0]: r[:5] for r in out}.values())
