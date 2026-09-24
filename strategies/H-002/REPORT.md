# H-002 report: momentum on the most liquid coins

**Verdict: fail. Rejected by its pre-registered kill criteria.** The best variant is the closest any strategy has come so far, but it fails two gates, and the variants disagree in sign.

Trials: T00024, T00026, T00028, T00030 (4 of the 8-trial budget). Development data 2020-01 to 2025-08, sessions 07:00 and 19:00 UTC, costs 2x unless noted.

## Key numbers

| Variant | Trades | Win rate | Net R 1x | Net R 2x | Sharpe 1x | Sharpe 2x | DSR, all 4 trials | Max DD 2x |
|---|---|---|---|---|---|---|---|---|
| 3 coins, 30 d | 786 | 49.4% | +52.4 | +39.0 | 1.01 | **0.75** | **0.75** | 12.9 R |
| 3 coins, 90 d | 751 | 45.4% | +23.0 | +8.8 | 0.44 | 0.16 | 0.23 | 27.0 R |
| 5 coins, 30 d | 1166 | 46.5% | +33.2 | +11.1 | 0.55 | 0.18 | 0.24 | 19.7 R |
| 5 coins, 90 d | 1039 | 45.7% | -0.4 | -21.6 | -0.01 | -0.35 | 0.03 | 48.6 R |

- PBO over the 4 variants: 0.09.
- Bars to beat: BTC buy-and-hold Sharpe 0.75, trend baseline B-001 Sharpe -0.05 (both 2x costs, same period).
- Null test, best variant: beats 99.4% of random-entry runs.
- Reaction delay, best variant: +6 h +35.4 R, +12 h +33.7 R at 2x. The edge does not need speed, so session mode fits it.
- Costs matter little here: 0.02 R per trade, as the precheck predicted.

## Regime split, best variant (2x costs)

| Regime | Net R |
|---|---|
| 2020-21 bull | +24.2 |
| 2022 crash | **-6.7** |
| 2023-24 recovery | +15.4 |
| 2025 (to Aug) | +6.1 |

## Kill criteria

- **Best variant fails a stage 3 gate:** it is negative in the 2022 regime, and its DSR is 0.75 < 0.95 once deflated by all 4 trials. Its own report shows 0.97, computed when it was the first trial; `harness.run.final` now gives the binding number. **Met.**
- **Variants disagree in sign of net R at 2x costs:** +39.0 to -21.6. No plateau. **Met.**
- It ties BTC buy-and-hold (0.75 vs 0.75); that is not "beats".

## What it means

- Short-lookback momentum on BTC, ETH and SOL is real and survives costs. It beats random entries decisively, has a small drawdown, and does not depend on reaction speed.
- It is not strong or stable enough to pass. It ties buy-and-hold, loses in the 2022 crash, and its result depends on the parameter choice.
- A new hypothesis built by tuning H-002 (other lookbacks, other coin counts) would be searching the same data further. The trial count would grow, and so would the deflation.

## Red flags

- This is the fourth idea tested on the same 2020-2025 data (H-001, B-001, H-002, plus the phase A exploration). Each new idea on the same data gets less trustworthy.
- The report's own DSR was misleading (see above). Fixed in the same PR as this report.

## Proposed next step

Close H-002. Do not tune it. See the lead report for the direction question.
