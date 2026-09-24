"""End-to-end session tests with a fake exchange: no network, no keys."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import ops.journal as journal_mod
import ops.session as session
from execution import okx, paper
from harness.pit import Panel

STRATEGY = '''
from datetime import timedelta
from harness.strategy import EntryRule, Signal

class Strategy:
    def __init__(self, params):
        self.side = params["side"]

    def on_bar(self, t, view):
        px = view.bars("BTC", n=1).close.iloc[-1]
        s = 1 if self.side == "long" else -1
        return [Signal("BTC", self.side, EntryRule("market"), px * (1 - s * 0.05), px * (1 + s * 0.15), timedelta(hours=72))]
'''

INST = {"BTC": {"instId": "BTC-USD_UM_XPERP-310404", "ct_val": 0.0001, "lot_sz": 1.0, "min_sz": 1.0, "tick_sz": 0.1, "max_lever": 50}}


def live_panel(t_sess, top_n=30):
    et = pd.date_range(t_sess - pd.Timedelta(days=60), t_sess - pd.Timedelta(hours=1), freq="h")
    px = 100_000 * np.ones(len(et))
    bars = pd.DataFrame({"event_time": et, "available_time": et + pd.Timedelta(hours=1), "open": px, "high": px * 1.001,
                         "low": px * 0.999, "close": px, "quote_volume": 1e9, "coin": "BTC"})
    return Panel({"BTC": bars}, {}, top_n=top_n)


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "strategies" / "T-001").mkdir(parents=True)
    (tmp_path / "strategies" / "T-001" / "strategy.py").write_text(STRATEGY)
    (tmp_path / "strategies" / "T-001" / "spec.yaml").write_text("id: T-001\nuniverse: {top_n: 5}\n")
    limits = dict(mode="paper", risk_per_trade_pct=0.5, max_open_risk_R=5, max_same_direction_R=3, daily_loss_stop_R=3,
                  weekly_loss_stop_R=6, drawdown_halt_R=20, max_leverage=5, liquidation_distance_x_stop=2,
                  max_consecutive_api_errors=3, strategies=[{"id": "T-001", "params": {"side": "long"}, "stage": "paper"}])
    monkeypatch.setattr(session, "ROOT", tmp_path)
    monkeypatch.setattr(session, "DATA", tmp_path)
    monkeypatch.setattr(journal_mod, "DB", tmp_path / "journal.db")
    monkeypatch.setattr(session, "Journal", lambda: journal_mod.Journal(tmp_path / "journal.db"))
    monkeypatch.setattr(session, "load_limits", lambda: limits)
    monkeypatch.setattr(session, "check_registered", lambda folder: None)
    monkeypatch.setattr(session.live_data, "panel", live_panel)
    monkeypatch.setattr(session.okx, "server_time_ms", lambda: pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    monkeypatch.setattr(okx, "xperp_instruments", lambda: INST)
    market = SimpleNamespace(bid=99_990.0, ask=100_010.0, last=100_000.0, bars=[])
    monkeypatch.setattr(okx, "ticker", lambda inst: (market.bid, market.ask, market.last))
    monkeypatch.setattr(okx, "candles", lambda inst, after_ms=None, limit=100: [] if after_ms else sorted(market.bars, reverse=True))
    monkeypatch.setattr(okx, "funding_history", lambda inst, since_ms: [])
    j = journal_mod.Journal(tmp_path / "journal.db")
    j.set("paper_equity_start", 100_000)
    return SimpleNamespace(journal=j, limits=limits, market=market, path=tmp_path)


ARGS = SimpleNamespace(dry_run=False, approve_all=True, resume=False)


def test_paper_session_places_once_then_stops_out(env):
    session.run(ARGS)
    t = env.journal.trades()
    assert len(t) == 1 and t[0]["status"] == "open" and t[0]["entry_px"] == 100_010.0  # market buy at the ask
    assert t[0]["contracts"] == 998 and t[0]["leverage"] == 5  # 1R = 500 USD over a 5010 USD stop distance
    session.run(ARGS)  # same session time: no second trade
    assert len(env.journal.trades()) == 1
    # A closed 1h candle that trades through the stop.
    ts = int(pd.Timestamp(t[0]["filled_at"]).timestamp() * 1000) // 3_600_000 * 3_600_000 + 3_600_000
    stop = t[0]["stop"]
    env.market.bars = [(ts, 100_000.0, 100_100.0, stop - 50, stop, True)]
    env.market.last = stop
    session.run(ARGS)
    t = env.journal.trades()[0]
    assert t["status"] == "closed" and t["exit_reason"] == "stop"
    r = env.journal.r_multiple(t)
    assert -1.2 < r < -1.0  # one R plus fees and half the spread


def test_halt_blocks_new_trades_and_keeps_positions(env):
    env.journal.set("halted", "test halt")
    session.run(ARGS)
    assert env.journal.trades() == []
    rep = next((env.path / "reports").glob("*")).read_text()
    assert "No new trades: halted" in rep


def test_risk_refusal_is_logged_as_veto(env):
    env.journal.set("paper_equity_start", 100)  # 1R = 0.5 USD: below the minimum order size
    session.run(ARGS)
    assert env.journal.trades() == []
    v = env.journal.db.execute("SELECT who, reason FROM vetoes").fetchall()
    assert v and v[0]["who"] == "risk" and "minimum order size" in v[0]["reason"]


class FakeBroker:
    mode = "live"

    def __init__(self, positions=(), pending=(), closed=(), protected=True):
        self._p, self._o, self._c, self._prot = list(positions), list(pending), list(closed), protected

    def positions(self):
        return self._p

    def pending(self):
        return self._o

    def closed_positions(self, since_ms):
        return self._c

    def protected(self, inst_id):
        return self._prot


def add(j, **kw):
    row = dict(id="x1", mode="live", strategy="T-001", coin="BTC", inst_id="I", side="long", kind="market", planned_entry=100.0,
               stop=95.0, target=115.0, contracts=10, ct_val=1.0, one_r_usd=50.0, risk_usd=50.0, leverage=5, time_limit_h=72,
               placed_at="2026-01-01T00:00:00+00:00", status="open", entry_px=100.0)
    row.update(kw)
    j.add_trade(**row)


def test_reconcile(tmp_path):
    j = journal_mod.Journal(tmp_path / "j.db")
    add(j)
    closed = [{"instId": "I", "closed_ms": 1_767_300_000_000, "pnl": -50.0, "fee": 1.0, "funding": 0.0, "close_px": 95.0}]
    assert session.reconcile(FakeBroker(closed=closed), j) == []
    t = j.trades()[0]
    assert t["status"] == "closed" and t["exit_reason"] == "stop" and j.r_multiple(t) == pytest.approx(-51 / 50)
    add(j, id="x2", inst_id="J")
    probs = session.reconcile(FakeBroker(positions=[{"instId": "J", "contracts": 7, "avg_px": 100, "upl": 0},
                                                    {"instId": "K", "contracts": 1, "avg_px": 1, "upl": 0}], protected=False), j)
    assert any("contracts" in p for p in probs) and any("without a stop" in p for p in probs) and any("does not know" in p for p in probs)
    add(j, id="x3", inst_id="L")
    assert any("gone from the exchange" in p for p in session.reconcile(FakeBroker(), j))


def test_tax_export_uses_last_ecb_rate_and_live_trades_only(tmp_path, monkeypatch):
    from ops import tax

    j = journal_mod.Journal(tmp_path / "j.db")
    monkeypatch.setattr(tax, "DB", tmp_path / "j.db")
    add(j, status="closed", closed_at="2026-03-08T10:00:00+00:00", filled_at="2026-03-06T10:00:00+00:00", exit_px=110.0,
        pnl_usd=100.0, fee_usd=2.0, funding_usd=-1.0)  # a Sunday
    add(j, id="p1", mode="paper", status="closed", closed_at="2026-03-08T10:00:00+00:00", pnl_usd=999.0)
    rates = pd.Series({pd.Timestamp("2026-03-05"): 1.10, pd.Timestamp("2026-03-06"): 1.25, pd.Timestamp("2026-03-09"): 2.0})
    path, rows = tax.export(2026, j, rates)
    assert len(rows) == 1 and rows[0]["net_pnl_usd"] == 97.0 and rows[0]["ecb_usd_per_eur"] == 1.25
    assert rows[0]["net_pnl_eur"] == round(97 / 1.25, 2) and path.exists()
