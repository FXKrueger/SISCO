# Decision log

Decided in the planning session on 2026-09-24. Do not reopen a decision without new evidence.
If new evidence appears, add a new entry that supersedes the old one. Do not edit old entries.

## D1. Market and holding period

- Decision: crypto perpetual futures on a liquid universe. Holding period 4 hours to about 7 days.
- Why: on-chain data only exists in crypto. Narratives and on-chain metrics play out over hours
  to days, so LLM latency (seconds) does not matter. Perps provide funding, open interest and
  liquidation data, and allow shorting. Sub-hour trading needs microstructure infrastructure
  where LLMs add little. Multi-week holds give too few trades to validate.
- Rejected: equities and FX, sub-hour trading, multi-week position trading.

## D2. Edge hypotheses and order

- Decision: baselines first (buy-and-hold BTC, trend rule), then A crowded positioning unwinds,
  then B LLM event extraction with post-event drift, then C narrative rotation. D on-chain flows
  only as candidate features.
- Why: A is cheap, has deep free history and no LLM leak problem. B is where the LLM adds real
  value. C needs expensive social data and more coins. Evidence for on-chain flows as a
  standalone edge is weak.
- Rule: every signal proves value on its own, after costs, before fusion.

## D3. Chart indicators

- Decision: Fibonacci levels are not in the core. They are the first hypothesis run through the
  harness (reversals at fib levels vs random levels). Named indicators enter only if they beat
  a raw feature set (returns over several lookbacks, realized volatility, volume, range).
  Trend-following is a mandatory baseline. Liquidity levels are part of A.
- Why: no theory behind fib ratios. Classic TA rules lose significance after correcting for data
  snooping (Sullivan, Timmermann and White 1999). Time-series momentum is the best-documented
  price effect in crypto (Liu and Tsyvinski 2021).

## D4. Validation harness

- Decision: one shared, enforced harness for every approach. Details in SPEC section 6.
- Covers: point-in-time data, survivorship, LLM memory leak, overlapping labels, multiple testing
  (DSR, PBO), costs at 1x and 2x, null tests, regime splits, crash stress, sealed holdout,
  canary self-tests, stage gates.

## D5. Risk unit and leverage

- Decision: 1R = 0.5% of equity. Max leverage 5x.
- Note: leverage sets margin, not risk. Risk = position size times stop distance.

## D6. Jurisdiction and tax

- Decision: Germany. Keep a per-trade EUR log for tax. Confirm treatment with a Steuerberater.
- Note: most advisers treat perp gains as capital income (section 20 EStG, flat rate).
  The one-year tax-free rule applies to spot only.

## D7. Purpose

- Decision: personal prop tool for the lead's own capital.
- Why: a product without a proven edge sells hope. Sharing signals decays the edge.
  Distribution would bring licensing questions (MiFID II) and can be revisited later.

## D8. LLM access

- Decision: top-tier Claude model (current Opus) through Claude Code in headless mode on the
  lead's subscription (`claude -p`, token via `claude setup-token`). Model ID and CLI version
  pinned. Codex CLI and Gemini CLI as optional second opinions. Small local models only for
  filtering and scraping-grade work. No paid LLM API unless the subscription path fails.
- Why: top quality for judgment, near-zero extra cost, official and permitted path for personal use.
- Rejected: scripting consumer chat apps or Computer Use as the pipeline backbone (terms of
  service, no pinned model, brittle). Small models for judgment work (lead's decision).

## D9. LLM funnel

- Decision: collect, filter locally, Opus extraction per story cluster, Opus deep dive on
  signal overlap, weekly recall audit of discarded items.
- Why: Opus quality without spending the subscription limits on noise.

## D10. Day-1 archives

- Decision: archive raw text, OKX X-Perps order books and funding, and liquidation streams
  from day 1.
- Why: clean LLM test data only builds up after the model cutoff. X-Perps history is short.
  Liquidation streams have no history. Archiving costs almost nothing.

## D11. Execution mode

- Decision: semi-automatic by default, full-auto toggle per strategy. Protection (exchange-side
  stops, limits, kill switch) always on. Every veto logged with a reason.
- Graduation criteria for full-auto: at least 50 live trades in which vetoes added nothing.
  Switching earlier is allowed and shown as an override.

## D12. LLM role

- Decision: the LLM is a feature extractor and may veto. It never makes or sizes a trade.
- Why: a statistical model gives calibrated, testable probabilities. LLM confidence is not
  calibrated. An LLM decision maker cannot be backtested before its cutoff, and every prompt
  change is a new strategy.

## D13. Signal fusion

- Decision: meta-labeling. Primary rules propose trades. A secondary model estimates
  P(target before stop) from context features. Triple-barrier labels with asymmetric targets.
  Regularized logistic regression first, LightGBM only if clearly better out of sample.
  P scales risk down from the 1R cap.
- Rejected: hand-weighted confluence scores (overfit by eye, no probabilities), one big ML model
  on all features (overfits noisy data, black box).

## D14. Risk limits

- Decision: max open risk 5R, same direction (BTC-beta adjusted) 3R, daily stop 3R, weekly stop
  6R, drawdown review at 20R (10% of equity), per-strategy kill at the 99th percentile of backtest
  Monte Carlo drawdown. Isolated margin, liquidation price at least 2x stop distance away.
- Stage gate thresholds: at 2x costs beat buy-and-hold BTC and the trend baseline risk-adjusted,
  DSR >= 0.95, regime robustness.

## D15. Venue

- Decision: OKX X-Perps as the main exchange. Execution layer is
  exchange-agnostic. Kraken Pro EU gets added when a strategy needs more coins (likely phase C).
  Hyperliquid for data only. Only the needed margin plus a buffer stays on the exchange.
- Why: OKX X-Perps is MiFID-regulated for EU retail and has an API. About 19 coins, which is
  enough for the baselines and A. Binance futures are closed to German retail, Bybit EU has no
  perps, Bitget has a BaFin warning.
- Consequence: research history comes from global venues (Binance, OKX global). Costs and
  slippage come from the X-Perps order book.

## D16. Data budget

- Decision: free sources first (SPEC section 5.2). X data decision deferred to phase C.

## D17. Infrastructure defaults

- Decision: Python, Parquet with DuckDB, one EU VPS or home server, Docker Compose,
  Telegram bot for alerts and trade approval.

## D18. Operating model

- Decision: the lead is the research lead and approver. Claude agents (the lead's and team
  members') do research, building, validation and testing, and report results. Maximum automation.
- Guardrails (SPEC section 12.2): protected paths, sealed holdout, automatic shared trial
  registry, pre-registration with trial budgets, independent verifier agent, deterministic
  gatekeeper, lead approval for promotions and limit changes. Agents may halt trading but
  never resume it.
- Why: automated research without guardrails becomes automated overfitting.

## D19. Session mode: no always-on server

Decided 2026-09-24. Supersedes the server parts of D10, D11 and D17.

- Decision: the system runs only when the lead runs it on their own devices. No VPS, no
  always-on host. Strategies decide at fixed session times (`harness/config.py`, default
  07:00 and 19:00 UTC). Backtests use exactly this schedule: time-limit exits and order expiries
  happen at the first session after the limit. Every entry goes out with its stop and target
  attached, so open positions are protected at the exchange between sessions. Each session
  catches up data, reconciles positions and checks the loss limits before any new trade.
- Archives (D10) run whenever the lead's Mac runs. Gaps are recorded, not prevented.
- Full-auto (D11) and Telegram alerts (D17) are deferred. Full-auto needs an always-on host.
- Every trial also runs with 6 h and 12 h reaction delay. An edge that needs faster reaction
  than sessions shows up there, with its price: an always-on host.
- Why: trading is semi-automatic, so no trade happens without the lead anyway. Stops and targets
  sit at the exchange. Holding periods are 4 h to 7 days. A server would mostly add maintenance
  and a process that can fail unattended. The harness keeps backtests honest about the schedule.
- Costs: liquidations during gaps are lost (can be bought later), phase B events are traded at
  the next session, not within minutes.
- Revisit: when a strategy fails only the delay test, or when full-auto qualifies.

## D20. Fixed holdout window

Decided 2026-09-24 by merging the harness (PR #9, #10).

- Decision: the holdout is the fixed window 2025-09-24 to 2026-09-24 (`harness/config.py`).
  Development data ends 2025-09-01; the three weeks between are an embargo. Data after
  2026-09-24 is forward data for paper trading and phase B, never development data.
- Why: a rolling "last 12 months" would lock every new archive away for a year.
- Consequence: the 10 October 2025 crash (SPEC 6.2 #9) is inside the holdout. Only the gatekeeper
  replays it.

## D21. Market orders on stop and target

Decided 2026-09-24 (PR #16).

- Decision: stop and target go to OKX attached to the entry, both as market orders on trigger.
  The cost model charges target exits taker fee plus spread, like stops.
- Why: OKX treats the attached stop and target as one OCO pair. A limit target that triggered but
  did not fill would cancel the stop and leave the position unprotected.

## D22. Guardrails in code, not only in rules

Decided 2026-09-24 (PR #16 and the hardening PR).

- Decision: the session reads `config/limits.yaml` (mode, limits, approved strategies) only as
  merged on origin/main, and refuses a different local copy. Registration, invalidations and
  verifier approvals are also read from origin/main. The agent guard blocks the gatekeeper, the
  holdout download, `--resume` and `--cashflow`; `--resume` also needs an interactive terminal.
- Why: CLAUDE.md rules 1, 2 and 7 hold even if an agent edits local files.
