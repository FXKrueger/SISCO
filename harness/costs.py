"""Cost model v1 (SPEC 6.2 #6). Protected: changes need the lead's approval.

All costs are fractions of notional. The engine converts them to R (divide by stop distance).
Funding is applied from real funding data, not here.

v1 limits, to be fixed in v2:
- Fees are OKX regular-tier perp fees. X-Perps fee tiers are an open item (SPEC 15).
- Spread and impact tiers come from only ~20 minutes of X-Perps books (research/xperps_costs.py,
  2026-09-24): a 25k USD market buy cost <1 bp on BTC/ETH, 4 bp on SOL/HYPE, 9-16 bp on
  XRP/DOGE/BNB/SUI/LINK/ADA, median 54 bp over the 116 instruments whose book could fill it.
  Re-measure once the archive has 2+ weeks. The tiers key on Binance volume because X-Perps
  has no history before 2026-04.
"""

MAKER_FEE = 0.0002
TAKER_FEE = 0.0005

# Half-spread plus impact for a ~25k USD order on X-Perps, by the coin's 30-day average daily
# quote volume on Binance (USD).
TIERS = [(5e9, 0.0001), (5e8, 0.0015), (1e8, 0.0030), (0, 0.0060)]
# Stops in fast markets: extra slippage as a share of the bar's high-low range.
STOP_RANGE_SLIP = 0.10


def half_spread(daily_quote_volume):
    return next(s for v, s in TIERS if daily_quote_volume >= v)


def entry_cost(kind, daily_quote_volume):
    return MAKER_FEE if kind == "limit" else TAKER_FEE + half_spread(daily_quote_volume)


def exit_cost(reason, daily_quote_volume, bar_range_pct=0.0):
    if reason == "target":
        return MAKER_FEE
    cost = TAKER_FEE + half_spread(daily_quote_volume)
    if reason == "stop":
        cost += STOP_RANGE_SLIP * bar_range_pct
    return cost
