# B-001 report: trend baseline

**Verdict: baseline recorded.** It is not promoted or killed. At 2x costs it loses money (Sharpe -0.21). That value is now the bar for gate 3 ("beats trend baseline"). Every strategy must beat it anyway, and must also beat BTC buy-and-hold (Sharpe 0.75).

Trial: T00012. The invalid T00002 is listed in `registry/invalidations.yaml`. Top 10 Binance perps, sessions 07:00 and 19:00 UTC, development data 2020-01 to 2025-08.

## Key numbers

| | 1x costs | 2x costs |
|---|---|---|
| Net R | +40.3 | -22.7 |
| Sharpe | 0.38 | -0.21 |

- Trades: 1405, win rate 43.9%.
- Gross plus funding: about +0.07 R/trade. Costs are 0.045 R/trade at 1x.
- Max drawdown at 2x costs: 67 R.
- DSR: 0.31.
- Null test: beats 95.5% of random-entry runs, just above the 95th percentile.
- Reaction delay: +6 h -44.7 R, +12 h -50.3 R at 2x costs. The small edge fades with slower entries.

## Regime split (2x costs)

| Regime | Net R |
|---|---|
| 2020-21 bull | +9.4 |
| 2022 crash | -3.0 |
| 2023-24 recovery | -23.8 |
| 2025 (to Aug) | -5.3 |

## Red flags

- The trend effect is real but small here: positive gross, beats the null test. It only survives low costs, and only in the 2020-21 bull market.
- 13,983 signals were skipped because of one position per coin and the 5R limit. The baseline re-enters coins that still trend, so a lot of signals repeat.
- The known-effect canary (BTC only, gross) has Sharpe 1.30. Most of the cost damage comes from alts in the top 10.

## Proposed next step

None for the baseline itself. Its numbers change only when the cost model changes (v2, after 2 weeks of X-Perps books). Then re-run it as a new trial, which needs a budget raise in its spec.
