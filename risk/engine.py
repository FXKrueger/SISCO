"""Risk engine (SPEC 10). Protected: changes need the lead's approval.

Pure functions, no I/O. The session tool feeds in the account state and a proposed trade and
gets back either a sized order or the reason it is refused. Checked before every order.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LIMITS_FILE = Path(__file__).parents[1] / "config" / "limits.yaml"


def load_limits(path=LIMITS_FILE):
    return yaml.safe_load(Path(path).read_text())


@dataclass
class Position:
    """An open position or a pending entry. Pending entries count as risk: they can all fill."""

    coin: str
    side: str  # long | short
    risk_R: float
    beta: float = 1.0  # to BTC


@dataclass
class Account:
    equity: float
    peak_equity: float
    available: float  # free margin
    positions: list[Position] = field(default_factory=list)
    pnl_today_R: float = 0.0  # UTC day, realized + open
    pnl_7d_R: float = 0.0  # rolling 7 days, realized + open
    halted: str | None = None  # reason; only the lead clears it


@dataclass
class Proposal:
    coin: str
    side: str
    entry: float
    stop: float
    target: float
    size_mult: float = 1.0  # stage multiplier (live_small: 0.25 then 0.5)
    beta: float = 1.0
    ct_val: float = 1.0  # contract size in base units
    lot_sz: float = 1.0
    min_sz: float = 1.0
    mmr: float = 0.01  # maintenance margin rate, conservative default


@dataclass
class Sized:
    contracts: float
    leverage: int
    risk_usd: float
    risk_R: float
    notional: float
    margin: float


def one_r(acct, limits):
    return acct.equity * limits["risk_per_trade_pct"] / 100


def halt_reason(acct, limits):
    """Conditions that stop all trading until the lead reviews (kill switch). None if clear."""
    if acct.halted:
        return acct.halted
    r = one_r(acct, limits)
    if r <= 0:
        return "equity is zero or negative"
    # Drawdown in R of the peak equity, so 20R = 10% from the peak (SPEC 10).
    if (acct.peak_equity - acct.equity) / (acct.peak_equity * limits["risk_per_trade_pct"] / 100) >= limits["drawdown_halt_R"]:
        return f"drawdown from peak >= {limits['drawdown_halt_R']}R"
    if acct.pnl_7d_R <= -limits["weekly_loss_stop_R"]:
        return f"7-day loss >= {limits['weekly_loss_stop_R']}R"
    return None


def check(p, acct, limits):
    """Returns (Sized, None) or (None, reason)."""
    if (h := halt_reason(acct, limits)):
        return None, f"halted: {h}"
    if acct.pnl_today_R <= -limits["daily_loss_stop_R"]:
        return None, f"daily loss >= {limits['daily_loss_stop_R']}R: no new trades today"
    s = 1 if p.side == "long" else -1
    if not (s * (p.entry - p.stop) > 0 and s * (p.target - p.entry) > 0):
        return None, "stop and target are not on opposite sides of the entry"
    if any(x.coin == p.coin for x in acct.positions):
        return None, "already a position or pending entry in this coin"

    r_usd = one_r(acct, limits)
    stop_dist = abs(p.entry - p.stop)
    lots = math.floor(r_usd * p.size_mult / stop_dist / p.ct_val / p.lot_sz + 1e-9)
    contracts = lots * p.lot_sz
    if contracts < p.min_sz:
        return None, f"below the minimum order size ({p.min_sz} contracts)"
    risk_usd = contracts * p.ct_val * stop_dist
    risk_R = risk_usd / r_usd

    if sum(x.risk_R for x in acct.positions) + risk_R > limits["max_open_risk_R"] + 1e-9:
        return None, f"total open risk would exceed {limits['max_open_risk_R']}R"
    exposure = sum((1 if x.side == "long" else -1) * x.risk_R * x.beta for x in acct.positions) + s * risk_R * p.beta
    if abs(exposure) > limits["max_same_direction_R"] + 1e-9:
        return None, f"BTC-beta adjusted exposure would exceed {limits['max_same_direction_R']}R in one direction"

    # Isolated margin. Liquidation is roughly 1/leverage - mmr away from entry; keep it at least
    # liquidation_distance_x_stop times the stop distance away.
    stop_pct = stop_dist / p.entry
    lev = min(limits["max_leverage"], math.floor(1 / (limits["liquidation_distance_x_stop"] * stop_pct + p.mmr)))
    if lev < 1:
        return None, "stop too wide for a safe liquidation distance"
    notional = contracts * p.ct_val * p.entry
    margin = notional / lev + notional * 0.001  # initial margin plus a buffer for the opening fee
    if margin > acct.available:
        return None, f"not enough free margin ({margin:.0f} needed, {acct.available:.0f} free)"
    return Sized(contracts, lev, risk_usd, risk_R, notional, margin), None
