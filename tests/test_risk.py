import pytest

from risk.engine import Account, Position, Proposal, check, halt_reason, load_limits

L = load_limits()


def acct(**kw):
    base = dict(equity=100_000, peak_equity=100_000, available=100_000)
    base.update(kw)
    return Account(**base)


def btc(side="long", entry=100_000, stop=95_000, target=115_000, **kw):
    kw.setdefault("ct_val", 0.0001)
    return Proposal("BTC", side, entry, stop, target, **kw)


def test_limits_file_matches_spec():
    assert (L["risk_per_trade_pct"], L["max_open_risk_R"], L["max_same_direction_R"]) == (0.5, 5, 3)
    assert (L["daily_loss_stop_R"], L["weekly_loss_stop_R"], L["drawdown_halt_R"], L["max_leverage"]) == (3, 6, 20, 5)
    assert L["mode"] in ("paper", "demo", "live") and isinstance(L["strategies"], list)


def test_sizing_one_r_and_leverage():
    s, why = check(btc(), acct(), L)
    assert why is None
    # 1R = 500 USD, stop 5000 USD per BTC -> 0.1 BTC = 1000 contracts of 0.0001
    assert s.contracts == 1000 and s.risk_usd == pytest.approx(500) and s.risk_R == pytest.approx(1)
    # stop 5%: 1/(2*0.05+0.01) = 9.09 -> capped at 5x
    assert s.leverage == 5 and s.margin == pytest.approx(10_000 / 5 + 10)  # plus the opening-fee buffer
    wide, _ = check(btc(stop=80_000, target=140_000), acct(), L)
    assert wide.leverage == 2  # stop 20%: 1/(0.4+0.01) = 2.4 -> 2x, liquidation far beyond the stop


def test_stage_multiplier_and_minimum_size():
    s, _ = check(btc(size_mult=0.25), acct(), L)
    assert s.risk_R == pytest.approx(0.25)
    _, why = check(btc(ct_val=1.0), acct(equity=1_000, peak_equity=1_000, available=1_000), L)
    assert "minimum order size" in why


def test_portfolio_limits():
    five = [Position(c, "long", 1.0, 0.0) for c in "ABCDE"]
    assert "total open risk" in check(btc(), acct(positions=five), L)[1]
    three_long = [Position(c, "long", 1.0, 1.0) for c in "ABC"]
    assert "BTC-beta" in check(btc(), acct(positions=three_long), L)[1]
    assert check(btc(side="short", stop=105_000, target=85_000), acct(positions=three_long), L)[1] is None  # hedging is fine
    assert "already" in check(btc(), acct(positions=[Position("BTC", "long", 1.0)]), L)[1]


def test_loss_limits_and_halts():
    assert "daily loss" in check(btc(), acct(pnl_today_R=-3), L)[1]
    assert halt_reason(acct(pnl_7d_R=-6), L).startswith("7-day loss")
    assert halt_reason(acct(equity=90_500, peak_equity=100_000), L) is None  # 9.5% = 19R of the peak
    assert "drawdown" in halt_reason(acct(equity=90_000, peak_equity=100_000), L)  # 10% = 20R
    assert "halted" in check(btc(), acct(halted="lead review"), L)[1]


def test_bad_geometry_and_margin():
    assert "opposite sides" in check(btc(stop=105_000), acct(), L)[1]
    assert "free margin" in check(btc(), acct(available=100), L)[1]
