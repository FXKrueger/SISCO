from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from harness import canaries, engine, registry, stats
from harness.pit import LookaheadError, Panel, PITView
from harness.strategy import EntryRule, Signal

T0 = pd.Timestamp("2022-01-01", tz="UTC")


def panel(rows, funding=()):
    """rows: (open, high, low, close) per hour from T0."""
    et = pd.date_range(T0, periods=len(rows), freq="h")
    o, h, l, c = map(list, zip(*rows))
    bars = pd.DataFrame({"event_time": et, "available_time": et + pd.Timedelta(hours=1), "open": o, "high": h, "low": l, "close": c, "quote_volume": 1e12})
    ft = [T0 + pd.Timedelta(hours=x) for x, _ in funding]
    f = pd.DataFrame({"event_time": pd.to_datetime(ft, utc=True), "available_time": pd.to_datetime(ft, utc=True), "funding_rate": [r for _, r in funding]})
    return Panel({"X": bars}, {"X": f}, top_n=1, min_days=0)


FLAT = [(100, 100.5, 99.5, 100)] * 3


def sig(side="long", stop=95, target=110, kind="market", price=None, hours=48):
    return Signal("X", side, EntryRule(kind, price, timedelta(hours=2)), stop, target, timedelta(hours=hours))


def test_pit_view_cuts_at_t_and_refuses_lookahead():
    p = panel(FLAT * 3)
    v = PITView(p, T0 + pd.Timedelta(hours=3))
    b = v.bars("X")
    assert len(b) == 3 and (b.available_time <= v.t).all()
    with pytest.raises(LookaheadError):
        v.bars("X", end=v.t + pd.Timedelta(hours=1))


def test_market_fills_at_next_open_and_target():
    p = panel(FLAT + [(101, 111, 100.5, 110)])
    tr = engine.simulate(T0 + pd.Timedelta(hours=3), sig(), p)
    assert tr["entry"] == 101 and tr["reason"] == "target" and tr["gross_R"] == pytest.approx(9 / 6)


def test_stop_wins_when_both_hit_in_one_bar():
    p = panel(FLAT + [(100, 111, 94, 100)])
    assert engine.simulate(T0 + pd.Timedelta(hours=3), sig(), p)["reason"] == "stop"


def test_gap_through_stop_fills_at_open():
    p = panel(FLAT + [(100, 100.5, 99.5, 100), (90, 91, 89, 90)])
    tr = engine.simulate(T0 + pd.Timedelta(hours=3), sig(), p)
    assert tr["exit"] == 90 and tr["gross_R"] == pytest.approx(-2.0)


def test_limit_not_filled_and_intrabar_fill_cannot_hit_target_same_bar():
    p = panel(FLAT + [(100, 100.5, 99.5, 100)] * 3)
    assert engine.simulate(T0 + pd.Timedelta(hours=3), sig(kind="limit", price=98), p) is None
    p = panel(FLAT + [(100, 111, 97, 100), (100, 100.5, 99.5, 100)])
    tr = engine.simulate(T0 + pd.Timedelta(hours=3), sig(kind="limit", price=98, hours=2), p)
    assert tr["entry"] == 98 and tr["reason"] == "time"


def test_funding_longs_pay_positive_rate_and_bad_signal_raises():
    p = panel(FLAT * 4, funding=[(8, 0.001)])
    tr = engine.simulate(T0 + pd.Timedelta(hours=3), sig(stop=90, target=120, hours=8), p)
    assert tr["funding_R"] == pytest.approx(-0.001 / 0.1)
    with pytest.raises(engine.SignalError):
        engine.simulate(T0 + pd.Timedelta(hours=3), sig(stop=105), p)


def test_pbo_high_for_noise_and_dsr_bounds():
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 1, (800, 10))
    assert 0.3 < stats.pbo(noise) < 0.8
    x = rng.normal(0.1, 1, 800)
    assert 0 <= stats.deflated_sharpe(x, [0.1, 0.0, -0.05]) <= stats.deflated_sharpe(x, [0.1])


def test_registry_chain_detects_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    monkeypatch.setattr(registry, "LOG", tmp_path / "trials.jsonl")
    registry.append({"hypothesis": "H-X", "kind": "start"})
    registry.append({"hypothesis": "H-X", "kind": "start"})
    registry.verify()
    lines = registry.LOG.read_text().splitlines()
    registry.LOG.write_text("\n".join([lines[0].replace("H-X", "H-Y"), lines[1]]) + "\n")
    with pytest.raises(RuntimeError):
        registry.verify()


def test_canaries():
    assert canaries.leak_canary() == "pass"
    assert canaries.random_canary().startswith("pass")


def test_evaluate_gates_report_pipeline(tmp_path, monkeypatch):
    from harness import run

    monkeypatch.setattr(registry, "ROOT", tmp_path)
    monkeypatch.setattr(registry, "LOG", tmp_path / "trials.jsonl")
    p = canaries.synthetic_panel(days=200)
    sigs = engine.generate(canaries.RandomEntries(), p, step_hours=6)
    t1, t2 = engine.backtest(sigs, p, 1.0), engine.backtest(sigs, p, 2.0)
    assert t2.net_R.sum() < t1.net_R.sum()
    r, series = run.evaluate(t1, t2, p, "H-X", p.timeline()[0])
    g, verdict = run.gates(r)
    assert verdict == "fail" and r["trades"] == len(t2) and r["null"] is not None
    tid = registry.append({"hypothesis": "H-X", "kind": "result"}, series)
    out = run.report(tmp_path, {"id": "H-X", "title": "random"}, tid, {}, r, g, verdict)
    assert "FAIL" in out.read_text() and registry.series("H-X").shape[1] == 1
