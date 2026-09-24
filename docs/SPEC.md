# SISCO: System Specification

Status: v1, 2026-09-24. This is the source of truth for humans and agents.
The reasons behind each choice are in [DECISIONS.md](DECISIONS.md).

## 1. Goal and scope

A personal trading system for the lead's own capital. It combines hard market data
(price, derivatives positioning, liquidity, on-chain) with context that an LLM extracts
from text (news, events, narratives). It trades crypto perpetual futures only when a
signal has a proven edge after costs.

- In scope: data collection, research, validation, paper trading, live trading on the lead's accounts.
- Out of scope for now: distribution to other users, managing money for others.

Success means at least one strategy passes every stage gate (section 6.4) and trades live
inside the risk limits (section 10). A system that correctly kills every idea is also
working as designed. It just has not found an edge yet.

## 2. Principles (non-negotiable)

1. No signal is trusted until the harness says so. Backtests outside the harness do not count.
2. Every signal proves value on its own, after costs, before it joins the combined model.
   Combining signals without edge only adds overfitting.
3. The LLM extracts structured features and may veto trades. It never decides or sizes a trade.
4. Point-in-time or nothing: every data point carries the moment it became known.
5. Every trial is counted, including failures.
6. Protection is automatic and cannot be switched off: stops at the exchange, risk limits, kill switch.
7. Cheapest path that keeps top quality: free data, local compute for bulk filtering,
   subscriptions for top-tier LLM work, paid APIs only when nothing else works.

## 3. Trading frame

- Market: crypto perpetual futures.
- Venue: OKX X-Perps (EU, MiFID-regulated, API v5 at `eea.okx.com`). Kraken Pro EU gets added
  when a strategy needs coins that OKX does not list. Hyperliquid is used for data only.
- Universe: at each point in time, the coins listed on the trading venue at that time,
  filtered by liquidity. About 19 coins on X-Perps as of 2026-09.
- Holding period: 4 hours to about 7 days.
- Leverage: max 5x. Leverage sets the margin, not the risk.
- Risk unit: 1R = 0.5% of equity.

## 4. Architecture

```
Sources -> Ingestion -> Point-in-time store (Parquet + DuckDB)
                              |
          +-------------------+--------------------+
          v                   v                    v
   Quant features      LLM event layer     Venue data (book, funding)
          +---------+---------+                    |
                    v                              |
      Primary rules (baseline, A, B, C)            |
                    v                              |
      Meta-model: P(win) -> size <= 1R             |
                    v                              v
             Risk engine -> Execution (semi-auto / auto) -> OKX
                    v
      Monitoring, kill switch, Telegram, reports

Research factory (agents) -> Harness -> Trial registry -> Gates -> Lead approval
```

Planned folders:

- `ingestion/`: one collector per source, scheduled, writes raw and normalized data.
- `store/`: Parquet files partitioned by source and date, queried with DuckDB.
- `features/`: pure functions from a point-in-time view to feature values.
- `llm/`: filter funnel, extraction jobs, output cache.
- `strategies/`: one folder per registered hypothesis (`strategies/H-###/`).
- `harness/`: backtest engine, cost model, labels, statistics, canaries, reports. Protected.
- `engine/`: meta-model and sizing.
- `risk/`: limits and checks. Protected.
- `execution/`: venue adapters, order management, reconciliation. Protected.
- `ops/`: monitoring, alerts, Telegram bot, tax export.
- `registry/`: trial registry. Append-only, written by the harness only.
- `config/`: `limits.yaml` (protected), `llm.yaml` (pinned model and CLI version).

## 5. Data

### 5.1 Point-in-time rules

Every row has `event_time`, `available_time`, `ingested_at`, `source`, `revision`.

- `available_time` is the earliest moment the system could have known the value.
  - Funding rate: at settlement. Predicted funding is a separate field with its own time.
  - Candle: at candle close.
  - News or posts: when our collector received the item, not the publish time.
  - LLM features: when the extraction finished.
- Revisions are stored as new rows. Nothing is overwritten.
- On-chain labels ("smart money", exchange wallets) are stored with the date the label was
  created. A label created later must never be used for an earlier decision.

### 5.2 Sources by phase (free first)

Phase 0 and A (quant core):

- Binance public data dumps (data.binance.vision): candles, aggregated trades, funding,
  premium index, open interest and long/short metrics. Deep history, no account needed.
- OKX global public API: candles, funding, open interest.
- OKX X-Perps (`eea.okx.com`): order book snapshots, trades, funding. Launched April 2026,
  so history is short. Record live from day 1. Feeds the cost and slippage model.
- Liquidation streams (Binance, OKX): live only, no history. Record from day 1.
- Hyperliquid public API: positions and liquidations are public on-chain.
- Coinalyze free tier: aggregated open interest, funding, liquidations.

Phase B (events):

- Exchange announcements (listings, delistings): OKX, Binance, Coinbase, Kraken, Bybit.
- Crypto news RSS feeds, GDELT.
- Telegram public channels via the official API (Telethon).
- Governance: Snapshot, Tally, project forums.
- Token unlock schedules, hack trackers (DefiLlama, rekt.news).
- Regulator press releases (SEC, CFTC, ESMA, BaFin) and the macro calendar (FOMC, CPI).

Phase C (narratives):

- Bluesky firehose, Farcaster hubs, Reddit, Telegram.
- X: no free legal path at scale. Decision deferred to phase C.

Phase D (on-chain features):

- Own BTC node, public ETH RPC or Etherscan free tier, DefiLlama (TVL, stablecoin flows), Dune free tier.
- Exchange netflows need labeled exchange wallets. Use open label sets, stored point-in-time.

### 5.3 Day-1 archives

These start before anything else, because the data cannot be downloaded later:

1. Raw text from all free phase B and C sources.
2. OKX X-Perps order book snapshots, trades, funding.
3. Liquidation streams (Binance, OKX, Hyperliquid).

## 6. Validation harness

### 6.1 Interface

Strategies never touch raw data or fills. They implement:

```python
class Strategy(Protocol):
    spec: RegisteredSpec  # pre-registered, hash-locked

    def on_bar(self, t: datetime, view: PITView) -> list[Signal]:
        """view only returns rows with available_time <= t."""


@dataclass
class Signal:
    coin: str
    side: Literal["long", "short"]
    entry: EntryRule            # limit or market, with expiry
    stop: float
    target: float
    time_limit: timedelta
    features: dict[str, float]  # snapshot for the meta-model
```

The harness owns fills, costs, funding, labels, statistics and reports.

### 6.2 Biases and countermeasures

1. Look-ahead: `PITView` enforces `available_time <= t`. Fills happen at the next bar at the
   earliest, plus latency.
2. Survivorship: the universe is rebuilt at every rebalance from data known then.
   Delisted and dead coins stay in the data.
3. LLM memory: only data after the model's training cutoff counts as evidence.
   Entity masking test: replace coin names and dates with placeholders. If the signal
   collapses, the model used memory. All LLM outputs are cached.
4. Overlapping labels: walk-forward testing and purged k-fold with embargo.
5. Multiple testing: the trial registry counts every run. Reports show the Deflated Sharpe
   Ratio (DSR) and the Probability of Backtest Overfitting (PBO, via CSCV) using the true
   trial count.
6. Costs: maker and taker fees, spread, slippage scaled by order size vs book depth
   (from X-Perps snapshots), funding, stop slippage in fast markets. Every test runs at
   1x and 2x costs.
7. Null test: 1000+ random-entry runs with the same timing, holding time and exposure.
   The signal must beat the 95th percentile.
8. Robustness: results split by regime (2018 bear, 2020-21 bull, 2022 crash,
   2023-24 recovery, 2025-26). Parameter heatmaps must show a plateau, not a spike.
9. Crash stress: replay March 2020, May 2021, November 2022 (FTX) and 10 October 2025,
   with realistic stop slippage.
10. Holdout: the last 12 months are sealed. Only the gatekeeper can read them, once per hypothesis.

### 6.3 Harness self-tests (canaries)

Run on every harness change and weekly:

- Leak canary: a strategy that reads one bar ahead. The harness must refuse or flag it.
- Random canary: random signals must return about zero minus costs.
- Known-effect canary: a simple trend rule must roughly match published results.

### 6.4 Stage gates

1. Registered: spec merged (hypothesis, economic reason, universe, rules, parameter grid,
   trial budget, kill criteria).
2. Development: walk-forward results, inside the trial budget (default 50 variants).
3. Statistics, all at 2x costs:
   - beats buy-and-hold BTC and the trend baseline, risk-adjusted,
   - DSR >= 0.95,
   - PBO <= 0.2 (default),
   - beats the null test,
   - holds in every regime split, or trades only in the regimes where it was shown to work.
4. Independent verification: the verifier agent's review passes.
5. Holdout: the gatekeeper runs it once. It must pass the same thresholds.
6. Paper trading: at least 3 months and 100 trades, whichever comes later. Live results
   must stay inside the backtest's expected range.
7. Small live (default): 0.25R per trade for the first 30 trades, then 0.5R for 30 trades.
8. Full size: 1R.

Promotion from step 5 onward needs the lead's approval. Kill criteria are written into the
spec before the first test.

## 7. Strategy roadmap

- Phase 0, baselines:
  - buy-and-hold BTC,
  - time-series trend rule (multi-lookback momentum with volatility-scaled size),
  - first registered hypothesis: do reversals happen more often at Fibonacci levels than at random levels?
- Phase A, crowded positioning unwinds:
  - inputs: funding extremes, open interest growth, perp-spot basis, liquidation clusters,
    long/short ratios, order book depth,
  - hypothesis: crowded leverage gets squeezed or liquidated. The edge comes from taking the other side.
- Phase B, LLM event extraction and post-event drift:
  - events: listings, delistings, unlocks, hacks, regulatory actions, governance votes, ETF flows, macro,
  - evaluation on data after the model's cutoff (forward) plus masked tests only.
- Phase C, narrative rotation:
  - map text to sectors, measure how fast attention grows, trade cross-sectionally,
  - needs Kraken for coverage and a decision on social data.
- Phase D, on-chain flows: candidate features inside A to C, not a standalone edge.

Chart indicators: the base feature set is raw (returns over several lookbacks, realized
volatility, volume, range). A named indicator (RSI, MACD, Bollinger, Fibonacci, ...) enters
only if it beats that raw set in the harness.

## 8. LLM layer

### 8.1 Access

- Judgment work uses the top-tier Claude model (the current Opus) through Claude Code
  in headless mode on the lead's subscription:
  `claude -p "<prompt>" --model <pinned full model ID> --output-format json`.
  The long-lived token for the server comes from `claude setup-token`.
- The full model ID and the Claude Code CLI version are pinned in `config/llm.yaml`.
  Changing either counts as a model change (section 8.4).
- Codex CLI and Gemini CLI: optional second opinions on high-impact events.
- Small local models: only for filtering, deduplication, embeddings and scraping-grade work.
- No paid LLM API unless the subscription path fails.

### 8.2 Funnel

1. Collect (no LLM).
2. Filter locally: deduplication, coin and entity matching, embedding relevance, story clustering.
3. Opus extraction per story cluster produces a structured event (schema below).
4. Opus deep dive when an event and a quant signal line up. Output: a short briefing plus risk flags.
5. Weekly recall audit: Opus reviews a random sample of discarded items. The report shows the miss rate.

Event schema:

```yaml
event_id: str
story_cluster_id: str
event_type: listing | delisting | unlock | hack | regulatory | macro | etf_flow | governance | partnership | other
coins: [str]
direction: float          # -1 bearish .. +1 bullish
surprise: float           # 0..1
credibility: float        # 0..1
time_sensitivity_h: float
source_ids: [str]
summary: str
model_id: str
cli_version: str
prompt_version: str
available_time: datetime  # when the extraction finished
```

Every output is cached with a hash of the prompt and input, the model ID and the CLI version.

### 8.3 Usage budget

- Daily call budget with a priority queue. When the limit is reached, jobs wait.
- Never fall back silently to another model. A different model is a different signal.
- Measure real usage in the first 2 weeks, then set the budget.

### 8.4 Model upgrades

- A new model or CLI version runs in shadow mode next to the current one for at least
  4 weeks (default).
- Its signals are validated again before they replace the old ones.
- The clean test window for LLM signals starts at the new model's training cutoff.

## 9. Decision engine

- Primary rules propose trades (direction, entry, stop, target, time limit).
- Meta-model: regularized logistic regression on context features predicts
  P(target is hit before stop). LightGBM only if it clearly beats the logistic model
  out of sample after the multiple-testing correction.
- Labels: triple barrier (target, stop, time limit). Targets are asymmetric, default
  2 to 3 times the stop distance.
- Sizing: risk = f(P), capped at 1R. Default tiers: P < 0.55 skip, 0.55 to 0.65 gives 0.5R,
  above 0.65 gives 1R. The final thresholds come from validation.
- Calibration: every tearsheet has a reliability plot and the Brier score.
- LLM veto: may block a trade. Every veto is logged and measured like a human veto.

## 10. Risk management

Limits (1R = 0.5% of equity):

| Limit | R | % of equity | Action |
|---|---|---|---|
| Risk per trade | 1R | 0.5% | hard cap |
| Total open risk | 5R | 2.5% | no new trades |
| Same direction, BTC-beta adjusted | 3R | 1.5% | no new trades in that direction |
| Daily loss (UTC day) | 3R | 1.5% | no new trades for the rest of the day |
| Weekly loss | 6R | 3% | full stop until the lead reviews |
| Drawdown from equity peak | 20R | 10% | all strategies pause, full review |
| Strategy live drawdown | 99th pct of backtest Monte Carlo | | that strategy stops |

Position rules:

- Isolated margin per position.
- The liquidation price must be at least 2 times the stop distance away from entry.
  Otherwise use lower leverage.
- The stop order is placed on the exchange right after the entry fills.

Custody: keep only the margin needed plus a buffer on the exchange. The rest stays off the exchange.

## 11. Execution

- Venue adapter interface. OKX X-Perps first, Kraken later.
- Semi-automatic by default. The trade card goes to Telegram: coin, side, entry, stop,
  target, size, P(win), briefing, evidence links. Approve, or reject with a reason.
  No answer before the entry expires means no trade.
- Full-auto toggle per strategy. The dashboard shows whether the graduation criteria are met
  (at least 50 live trades in which vetoes added nothing). Switching earlier is allowed but
  is shown as an override.
- Always on, in both modes: exchange-side stops, risk limits, reconciliation loop (every
  minute, actual positions vs expected), kill switch.
- Kill switch triggers: loss limits, stale data (default: a live feed silent for more than
  5 minutes), repeated API errors, reconciliation mismatch.
  Action: cancel open entries, keep stops, alert the lead. Only the lead can resume.
- Veto log: every human and LLM veto is recorded with a reason. The harness reports whether
  vetoes add or destroy value.

## 12. Research factory (agents)

Operating model: the lead sets direction and approves. Claude agents, the lead's and those
of team members, do the work and report results. Everything is as automated as possible,
behind guardrails that agents cannot bypass. Automated research without guardrails turns
into automated overfitting: an agent asked to find a profitable strategy will keep trying
until a backtest looks good.

### 12.1 Roles

- Orchestrator agent: keeps the hypothesis backlog (GitHub issues), assigns work, writes the weekly report.
- Data agents: build and maintain collectors, archives and data-quality checks.
- Research agents: take one hypothesis, write the spec, implement the strategy, run it through
  the harness within the trial budget, report.
- Verifier agent: fresh context, adversarial, ideally from a different vendor (for example Codex).
  Reviews every result that passes the development gate: leak audit, code review for future
  data access, canary reruns, sensitivity checks.
- Gatekeeper: a deterministic service, not an agent. Runs the holdout once, computes DSR and
  PBO with the registry's trial counts, posts the verdict.
- Ops agent: watches the live system, writes the daily report, triages incidents.
  It can halt trading. It cannot resume trading or raise limits.
- Lead (human): approves changes to harness, risk and execution, promotions from the holdout
  step onward, full-auto toggles and limit changes.

### 12.2 Guardrails

1. Protected paths: `harness/`, `risk/`, `execution/`, `config/limits.yaml` and the cost model.
   Changes need the lead's review (CODEOWNERS plus branch protection on GitHub).
2. Sealed holdout: stored on the server outside the repo. Only the gatekeeper can read it.
3. Automatic trial registry: the harness logs every run itself (spec hash, parameters, data
   range, results). Agents cannot edit or delete entries. One registry for everyone, so the
   trials of every team member count.
4. Pre-registration: the harness refuses to run a spec that is not merged. Each hypothesis has
   a trial budget (default 50 variants). When the budget is used up, the hypothesis is closed.
5. One report template for every result. Failures are reported like successes.
6. LLM features come only from the cached extraction pipeline. Strategy code makes no LLM calls.

### 12.3 Workflow per hypothesis

1. Issue `H-###` with the hypothesis and its economic reason.
2. Pull request with `strategies/H-###/spec.yaml`. The merge is the registration (timestamp and hash).
3. Research runs go through the harness. The registry logs everything.
4. The harness generates the tearsheet.
5. Development gate passed: verifier review.
6. Verifier passes: gatekeeper holdout run.
7. Lead approves: paper trading, then small live, then full size.

### 12.4 Report to the lead (one page)

- Hypothesis and verdict: pass, fail or inconclusive.
- Key numbers: net Sharpe at 1x and 2x costs, DSR, PBO, max drawdown in R, number of trades, trials used.
- Calibration, regime split, null test result.
- Red flags from the verifier.
- Proposed next step.

## 13. Operations

- Infrastructure: one EU VPS or home server, Docker Compose, Python. Nightly backup of the
  archives to object storage.
- Monitoring: a heartbeat per collector and service, alerts to Telegram.
- Reports: daily ops report, weekly research report.
- Tax: per-trade export (time, instrument, side, size, price, fees, funding, PnL in EUR with the
  EUR/USD rate at execution time). Tax treatment to be confirmed with a Steuerberater.

## 14. Milestones (estimates)

- M0, week 1: repo scaffold, agent rules, day-1 archives running.
- M1, weeks 1-3: point-in-time store, harness core, cost model v1, trial registry, canaries
  passing. The Fibonacci test is the first registered hypothesis.
- M2, weeks 4-8: baselines and phase A research, verifier, gatekeeper.
- M3, from about week 8: risk engine, OKX adapter, Telegram semi-auto flow, kill switch.
  Paper trading for anything that passed.
- M4, in parallel from about week 6: phase B funnel and extraction. Forward evaluation data builds up.
- M5: small live for strategies that pass paper trading. Phase C decision (X data, Kraken).

## 15. Open items to verify

- OKX X-Perps: fee tiers, max leverage per coin, minimum order size, API rate limits, demo
  trading. Without demo trading, paper trading uses shadow fills on the live X-Perps order book.
- Training cutoff of the pinned Opus model. It defines the clean test window for phase B.
- Real subscription usage limits under pipeline load.
- Tax classification of X-Perps (5-year expiry futures) in Germany.
- X data access for phase C.
- Kraken EU account when phase C starts.
- GitHub setup: CODEOWNERS and branch protection for the protected paths.
