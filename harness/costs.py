"""Cost model v1 (SPEC 6.2 #6). Protected: changes need the lead's approval.

All costs are fractions of notional. The engine converts them to R (divide by stop distance).
Funding is applied from real funding data, not here.

v1 limits, to be fixed in v2:
- Fees are OKX regular-tier perp fees. X-Perps fee tiers are an open item (SPEC 15).
- Spread and impact tiers from ~3 hours of X-Perps books (research/xperps_costs.py, 168 one-minute
  samples, 2026-09-24), mapped to the coin's Binance volume in the last 90 days of dev data:
  >= 5B (BTC, ETH) 0.3-0.9 bp; 1-5B (SOL, XRP, DOGE) 3-12 bp; 0.2-1B (SUI, ADA, BNB, LINK, AVAX,
  UNI, BCH, ...) median ~15 bp with outliers to 78 bp; thinner books median ~54 bp.
  Re-measure once the archive has 2+ weeks. The tiers key on Binance volume because X-Perps
  has no history before 2026-04. Binance volumes were lower in 2020-21, so old trades land in
  more expensive tiers: conservative.
"""

MAKER_FEE = 0.0002
TAKER_FEE = 0.0005

# Half-spread plus impact for a ~25k USD order on X-Perps, by the coin's 30-day average daily
# quote volume on Binance (USD).
TIERS = [(5e9, 0.0001), (1e9, 0.0008), (2e8, 0.0015), (0, 0.0040)]
# Stops in fast markets: extra slippage as a share of the bar's high-low range.
STOP_RANGE_SLIP = 0.10


def half_spread(daily_quote_volume):
    return next(s for v, s in TIERS if daily_quote_volume >= v)


def entry_cost(kind, daily_quote_volume):
    return MAKER_FEE if kind == "limit" else TAKER_FEE + half_spread(daily_quote_volume)


def exit_cost(reason, daily_quote_volume, bar_range_pct=0.0):
    # Targets are market orders on trigger, like stops (execution/okx.py: a limit target in an OCO
    # pair could leave a position without a stop), so they pay taker fee and spread too.
    cost = TAKER_FEE + half_spread(daily_quote_volume)
    if reason == "stop":
        cost += STOP_RANGE_SLIP * bar_range_pct
    return cost
