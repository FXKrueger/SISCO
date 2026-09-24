# B-001 report: trend baseline

**Verdict: baseline recorded.** It is not promoted or killed. At 2x costs it breaks even (Sharpe -0.05). That value is the bar for gate 3 ("beats trend baseline"). Every strategy must also beat BTC buy-and-hold (Sharpe 0.75).

Current trial: T00022, with cost tiers corrected after review 1 (`docs/reviews/2026-09-24-gemini.md`). T00012 used the old tiers, which were about 2x too high for mid-sized coins, and stays in the record. The invalid T00002 is listed in `registry/invalidations.yaml`. Top 10 Binance perps, sessions 07:00 and 19:00 UTC, development data 2020-01 to 2025-08.

## Key numbers (T00022)

| | 1x costs | 2x costs |
|---|---|---|
| Net R | +47.3 | -5.3 |
| Sharpe | 0.45 | -0.05 |

- Trades: 1403, win rate 44%.
- Max drawdown at 2x costs: 64.5 R.
- Null test (random coin and side through the engine, same stop, target and time limit): the strategy beats 100% of 1000 runs; their 95th percentile is -61 R.
- Reaction delay at 2x costs: +6 h -23.1 R, +12 h -28.6 R.
- DSR 0.40 and PBO 0.00 are not meaningful here. The two "trials" are the same rule under two cost models, not variants.
- For comparison, T00012 (old tiers): +40.3 R at 1x and -22.7 R at 2x, Sharpe 0.38 and -0.21.

## Regime split (2x costs)

| Regime | Net R |
|---|---|
| 2020-21 bull | +16.9 |
| 2022 crash | -2.8 |
| 2023-24 recovery | -15.6 |
| 2025 (to Aug) | -3.8 |

## Red flags

- **The trend effect is real:** it beats random entries decisively. But it only pays in the 2020-21 bull market and roughly breaks even at 2x costs.
- **7-day churn:** the 7-day holding limit (SPEC 3) closes and re-enters coins that still trend, paying costs each time (review 1, finding 10).
- **Cost model:** still based on hours of X-Perps order books, not weeks.

## Proposed next step

None for the baseline itself. Re-measure it when cost model v2 exists, after 2 weeks of X-Perps books.
