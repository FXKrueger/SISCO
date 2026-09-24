## Ranked List of Findings

### 1. P0: Unattainable $5B volume tier overcharges liquid coins (BTC/ETH) by 15x on spread
- **File:line**: [`harness/costs.py:20`](../../harness/costs.py#L20)
- **What is wrong**: The top tier in `TIERS` requires a 30-day average daily quote volume of at least $5 billion USD (`5e9`) to qualify for a 1 bp (`0.0001`) half-spread. In crypto reality, 30-day ADV on Binance rarely reaches $5B even for BTC (typically $1B to $3B), and ETH is typically $500M to $1.5B. Consequently, BTC and ETH almost never reach tier 1 and fall into tier 2 (`5e8`, 15 bp half-spread). This charges BTC and ETH a 15 bp half-spread (30 bp at 2x costs) instead of <1 bp, directly contradicting the empirical data cited in `costs.py` lines 8-12 ("a 25k USD market buy cost <1 bp on BTC/ETH"). This artificially penalizes liquid coins by 15x on spread and caused the B-001 trend baseline to collapse into negative net Sharpe.
- **Concrete failure scenario**: A strategy trading BTC futures on Binance historical data has an ADV of $2.5B. Because $2.5B < $5B, `half_spread()` selects 15 bp. At 2x costs, entry and exit take 30 bp each, plus 10 bp fees, totaling 80 bp round trip on a Bitcoin trade that actually costs ~6 bp on OKX X-Perps. Over hundreds of trades, this wipes out the strategy's entire genuine edge.
- **Verification**: Verified by reading only.

---

### 2. P0: Null test design conditions on strategy outcomes and has unbounded payoff
- **File:line**: [`harness/stats.py:79-92`](../../harness/stats.py#L79-L92)
- **What is wrong**: The null test implementation has three compounding flaws that invalidate it:
  1. It conditions on the strategy's realized holding time (`b = s.asof(tr.exit_time - pd.Timedelta(hours=1))`). Trade holding time is endogenous: losing trades stop out quickly, while winning trades run. Imposing the strategy's realized exit timestamp onto random entries conditions on the outcome.
  2. For any trade that entered and stopped out within the same 1-hour bar, `tr.exit_time - pd.Timedelta(hours=1)` equals `entry_time`. Thus `a == b`, `log(b/a) = 0`, and the null trade always returns exactly 0 plus the trade's cost.
  3. The null trades have no stops or targets. A real trade has asymmetric payoff bounded between -1R and +2.5R (or +3R). The null trade return is `log(b/a) / stop_pct`. If a coin moves 10% over 72 hours with a 0.5% stop distance, the null trade gets +/-20R. This blows out the null distribution's variance, rendering the 95th percentile hurdle meaningless.
- **Concrete failure scenario**: In H-001, many trades hit their tight 1-ATR stop in the entry bar. For every such trade, `s.asof(tr.entry_time)` and `s.asof(tr.exit_time - 1h)` fetch the exact same price. The null test assigns a return of 0.0 gross, subtracts real trade costs, and compares this synthetic artifact against the strategy. Meanwhile, for trades held 72 hours, random price swings generate extreme +/-15R returns because no stops exist to cap losses.
- **Verification**: Verified by reading only.

---

### 3. P1: Funding rate is charged on positions already closed prior to settlement
- **File:line**: [`harness/engine.py:113, 120`](../../harness/engine.py#L113)
- **What is wrong**: In `simulate()`, line 113 sets `exit_time = pd.Timestamp(et[k], tz="UTC") + pd.Timedelta(hours=1)`. Line 120 queries funding using `m = (f.event_time > entry_time) & (f.event_time <= exit_time)`. Binance funding settles at 00:00, 08:00, and 16:00 UTC. If a position stops out or hits its target at 07:15 UTC (inside the 07:00 bar `k`), `exit_time` is set to 08:00:00 UTC. Because of `<= exit_time`, the trade is assessed the 08:00 funding settlement even though the position was liquidated 45 minutes before settlement occurred.
- **Concrete failure scenario**: A long position enters at 07:00 UTC and stops out at 07:10 UTC. At 08:00 UTC, funding settles at +0.03% (longs pay shorts). The engine assigns `exit_time = 08:00 UTC`, matches the 08:00 settlement, and deducts funding from the trade.
- **Verification**: Verified by reading only.

---

### 4. P1: Asymmetric intrabar rules penalize limit order targets
- **File:line**: [`harness/engine.py:98-101`](../../harness/engine.py#L98-L101)
- **What is wrong**: For a limit order that fills inside bar `j` (`intrabar_fill = True`), line 92 checks if stop was hit on any low/high touch (`l[k] <= sig.stop`), but line 101 overrides target hit to require `side * (c[k] - sig.target) >= 0` (the bar close must be beyond the target). If price fills a limit order, runs past the profit target, and pulls back slightly before the candle close, the stop loss can trigger on touch, but the profit target is completely ignored.
- **Concrete failure scenario**: On a long limit buy at 100 with stop at 98 and target at 105: the 1-hour bar opens at 101, dips to 99 (filling the limit at 100), rallies to 106 (touching the 105 target), and closes at 104.5. Because `c[k] = 104.5 < 105`, `hit_tgt` is set to False. The trade is not closed at the target. In the next bar, price falls to 98 and stops out.
- **Verification**: Verified by reading only.

---

### 5. P1: Arbitrary universe ordering biases concurrency and skips signals
- **File:line**: [`harness/engine.py:137-147`](../../harness/engine.py#L137-L147)
- **What is wrong**: In `backtest()`, signals are sorted by time: `sorted(signals, key=lambda x: x[0])`. At any given session `t`, all signals share the exact same timestamp. Python's stable sort preserves their ordering from `view.universe()`. Unfilled limit orders occupy a slot in `busy` until expiry (`next_session`). When `len(busy) >= max_open` (5 positions), all subsequent signals at that session are skipped. Coins listed earlier in the universe array take all slots, permanently starving coins that appear later in the list.
- **Concrete failure scenario**: At 07:00 UTC, 10 coins produce signals. Coins 1 to 5 emit limit orders that do not fill during the session. Coins 6 to 10 emit market orders that would have been profitable trades. Because coins 1 to 5 fill the 5 slots with pending limit orders, coins 6 to 10 are discarded with `skipped_signals` incremented.
- **Verification**: Verified by reading only.

---

### 6. P1: Reaction delay testing distorts risk geometry and invalidates stops
- **File:line**: [`harness/engine.py:58, 61, 68, 85`](../../harness/engine.py#L58)
- **What is wrong**: When `delay_h` is tested (e.g. 6 hours), line 58 shifts the fill index `i` by 6 hours, filling market orders at `o[i]`. However, `ref` (planned entry), `sig.stop`, and `sig.target` are not updated; they remain frozen at the values generated at time `t`. If price moves past the stop or target during the 6-hour delay, `risk` (`abs(ref - sig.stop)`) is detached from the fill price `entry` (`o[i]`), causing immediate stop-outs or inverted reward-to-risk profiles.
- **Concrete failure scenario**: At 07:00 UTC, BTC is at 100; a long signal has stop at 98 and target at 106 (`risk = 2`). Under `delay_h = 6`, the order fills at 13:00 UTC. By 13:00, BTC has dropped to 97. The trade enters at 97 with a stop at 98. Because entry is already below the stop, the stop triggers instantly on the entry bar, booking an artificial loss.
- **Verification**: Verified by reading only.

---

### 7. P2: `PITView._cut()` ignores the caller's `end` argument
- **File:line**: [`harness/pit.py:95-98`](../../harness/pit.py#L95-L98)
- **What is wrong**: In `PITView._cut(avail, n, end)`, line 95 asserts that `end` is not in the future (`> self.__t`). However, `hi` is computed strictly using `self.__t` (`hi = np.searchsorted(avail, self.__t, side="right")`). The `end` argument is never used to constrain `hi`. Any call requesting data up to a past cutoff `end` receives data all the way up to `self.__t`.
- **Concrete failure scenario**: A strategy calls `view.bars("BTC", end=t - timedelta(days=7))` expecting history up to 7 days prior. `_cut` ignores `end` and returns all bars up to `t`.
- **Verification**: Verified by reading only.

---

### 8. P2: Pre-registration integrity check does not verify imported modules
- **File:line**: [`harness/run.py:43-50`](../../harness/run.py#L43-L50)
- **What is wrong**: `check_registered()` verifies only that `spec.yaml` and `strategy.py` match `origin/main`. If `strategy.py` imports local helper files (e.g. `from .helpers import ...`), those auxiliary files are never verified against `origin/main`. An agent can alter strategy behavior without committing changes to `origin/main`.
- **Concrete failure scenario**: A research agent creates `strategies/H-002/helper.py` containing key signal generation logic and imports it into `strategy.py`. The agent modifies `helper.py` locally to curve-fit parameters. `python -m harness.run` executes without error because `check_registered` only inspects `strategy.py` and `spec.yaml`.
- **Verification**: Verified by reading only.

---

### 9. P2: Static regex lint can be bypassed without AST inspection
- **File:line**: [`harness/run.py:30, 52-56`](../../harness/run.py#L30)
- **What is wrong**: The `FORBIDDEN` list relies on simple regex pattern matching (`\bopen\(`, `\bimport os\b`, `read_parquet`, etc.). It can be bypassed using standard Python dynamic attribute and module loading techniques that do not match the token patterns.
- **Concrete failure scenario**: Strategy code executes `__import__("pathlib").Path("data/store").glob("*")` or accesses `sys.modules["os"]`. The regex patterns do not trigger, and unauthorized file access occurs.
- **Verification**: Verified by reading only.

---

### 10. P2: B-001 trend baseline incurs artificial churn from 7-day time limits
- **File:line**: [`strategies/B-001/strategy.py:29-30`](../../strategies/B-001/strategy.py#L29-L30)
- **What is wrong**: B-001 applies a 7-day time limit (`timedelta(days=7)`). When a coin is in a sustained multi-month trend, the position exits at 7 days, paying taker fee plus half-spread, and immediately re-enters at the same session, paying another taker fee plus half-spread. Over a 5-year backtest across 10 coins, this churn generated 1,405 trades and 13,983 skipped signals, causing severe transaction cost drag on a trend-following rule.
- **Concrete failure scenario**: BTC trends upward continuously for 90 days. Instead of maintaining one continuous position, B-001 closes and re-opens the position every 7 days, paying 13 separate round-trip transaction costs instead of 1.
- **Verification**: Verified by reading only.

---

### 11. P3: Strict `min_periods` in `liquidity()` causes 30-day dropouts on single-day gaps
- **File:line**: [`harness/pit.py:26-28`](../../harness/pit.py#L26-L28)
- **What is wrong**: `vol30 = daily.rolling(30, min_periods=min_days).sum().shift(1)` sets `min_periods=30`. If an exchange outage or ingestion issue causes a single missing day (`NaN`) in the 30-day rolling window, the entire 30-day rolling sum evaluates to `NaN`. The coin is dropped from the universe for the next 30 days regardless of its actual liquidity.
- **Concrete failure scenario**: A major coin with $1B daily volume has one missing day due to an API glitch. `daily` records `NaN` for that day. For the subsequent 30 days, `vol30` evaluates to `NaN`, and the coin vanishes from `view.universe()`.
- **Verification**: Verified by reading only.

---

### 12. P3: Exit transaction cost tier uses ADV from signal time instead of exit time
- **File:line**: [`harness/engine.py:115-116`](../../harness/engine.py#L115-L116)
- **What is wrong**: In `simulate()`, line 115 evaluates `adv = panel.adv(sig.coin, t)` using the signal timestamp `t`. Line 116 computes both entry cost and exit cost using that same `adv`. For positions held up to 7 days, the exit fee tier is determined by liquidity known at entry rather than liquidity at exit.
- **Concrete failure scenario**: A position is held for 7 days during which the coin's volume drops from tier 2 to tier 3. The exit cost still applies tier 2 pricing.
- **Verification**: Verified by reading only.

---

## Overall Verdict

### Can the H-001 conclusions be trusted?
**YES.**
The conclusion that Fibonacci retracement levels do not produce reversals more often than random levels is sound. Although the strategy was doomed after costs due to an hourly ATR stop distance, the gross win rate was ~28.5%, virtually identical to the random level control (28.6% to 28.8%) and the theoretical expectation of a random walk (28.57%). Even with zero transaction costs, the gross edge was zero (-0.01 to -0.02 R/trade). The rejection of H-001 is scientifically valid.

### Can the B-001 conclusions be trusted?
**NO.**
The reported baseline Sharpe of -0.21 at 2x costs is distorted and should not serve as the benchmark hurdle for Gate 3. It collapsed into negative returns primarily due to the cost model bug in [`harness/costs.py:20`](../../harness/costs.py#L20) (the $5B volume threshold that overcharged BTC and top-10 coins by 15x on spread) combined with the artificial 7-day position churn in [`strategies/B-001/strategy.py:29`](../../strategies/B-001/strategy.py#L29). At realistic costs matching empirical X-Perps order book data, B-001 is net positive. Recording -0.21 as the baseline hurdle compromises Gate 3 by allowing losing strategies to pass.
