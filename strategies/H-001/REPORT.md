# H-001 report: Fibonacci levels vs random levels

**Verdict: fail. Hypothesis rejected, closed.** Fibonacci retracement levels do not produce more reversals than random levels. Per D3 they do not enter the feature set.

Trials: T00014, T00016, T00018, T00020 (4 of the 8-trial budget; the invalid T00004-T00010 are listed in `registry/invalidations.yaml`). Development data 2020-01 to 2025-08, top 20 Binance perps by liquidity at each point in time, sessions at 07:00 and 19:00 UTC.

## Key numbers

| Variant | Trades | Win rate | Net R 1x | Net R 2x | Sharpe 2x | DSR | PBO | Max DD 2x |
|---|---|---|---|---|---|---|---|---|
| fib, 240 h | 8740 | 28.05% | -2851 | -5507 | -9.15 | 0.00 | n/a | 5523 R |
| random, 240 h | 8683 | 28.76% | -2652 | -5314 | -9.06 | 0.00 | 1.00 | 5323 R |
| fib, 120 h | 9335 | 28.51% | -2715 | -5361 | -9.09 | 0.00 | 0.92 | 5370 R |
| random, 120 h | 9290 | 28.57% | -2748 | -5449 | -9.13 | 0.00 | 0.99 | 5457 R |

PBO is n/a for the first trial of a hypothesis. It needs at least 2 trials.

## Kill criteria (from the spec)

- Win rate, fib vs random, one-sided two-proportion z-test:
  - 240 h: z = -1.03, p = 0.85;
  - 120 h: z = -0.09, p = 0.54.
  - Not better. **Criterion met.**
- Net R at 2x costs, fib vs random: lower at 240 h, higher by 87 R at 120 h. Both variants lose more than 5,000 R. **Criterion met.**
- Stage 3 gates: all failed. **Criterion met.**

## Why it loses

- **No gross edge:** gross plus funding is -0.02 R/trade (240 h) and -0.01 R/trade (120 h). The win rate sits at break-even for a 2.5R target (28.6%).
- **Costs are the loss:** 0.28-0.30 R/trade at 1x costs. The stop is 1 hourly ATR, often below 1% of price. With X-Perps spreads of 15-60 bp on alts, a round trip costs about a third of R.
- **Regimes:** it loses in every regime, from -341 R to -2,207 R.
- **Reaction delay:** +6 h and +12 h lose less (-4,571 and -3,278 R at 2x). Fewer orders fill, so there are fewer trades, not a better edge.
- **Null test:** the strategy beats 23-54% of random-entry runs. It does no better than chance.

## Red flags

- Cost model v1 rests on 20 minutes of order books. It does not change the verdict: even at 0 costs the gross result is about 0.
- Lesson for the next hypotheses: tight stops meet X-Perps spreads. Costs in R = cost in % / stop in %, so specs with hourly-ATR stops on alts cannot win.

## Proposed next step

Close H-001. Record in DECISIONS that fib levels stay out (D3 follow-up). Phase A specs should use daily-scale stops and the most liquid coins.
