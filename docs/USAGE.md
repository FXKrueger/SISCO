# SISCO usage guide

What you do, and when. The system acts only while a session runs (D19).

## Every day: two sessions

Session times are 07:00 and 19:00 UTC: 09:00 and 21:00 in German summer time, 08:00 and 20:00
in winter. Run a session at or after a session time. A later run uses the data known at the
session time, never newer data, so the result is the same as in the backtest.

```bash
cd $REPO && git pull && .venv/bin/python -m ops.session
```

A session does this, in order:
1. checks the clock against the exchange and reconciles positions and orders with the journal;
2. checks the loss limits and the kill switch;
3. closes positions past their time limit and cancels expired entries;
4. extracts new LLM events (up to 10 stories);
5. computes signals for the strategies you approved, and only those whose files still match
   their registered version;
6. runs the risk check, then shows a trade card for each trade.

For each card you answer `y`, or `n` plus a reason. Every rejection is logged. The harness later
measures whether your vetoes added or destroyed value. At the end the report is printed and saved
to `data/reports/`.

Missed a session? Nothing happens between sessions. Open positions are protected by their
exchange-side stop and target. The next session catches up: time exits, cancelled entries, fills.

Just looking: `--dry-run` shows the cards and places nothing. It does not use up the session.

## When the kill switch fires

The report says `HALTED` and why. Possible reasons:
- a 7-day loss of 6R or more;
- a drawdown of 20R (10%) from the peak;
- repeated API or order errors;
- the exchange and the journal disagree;
- an open position has no stop at the exchange.

What the system then does: it cancels open entries and keeps the stops. It will not trade again
until you resume.

What you do:
1. Read the report. Look at the account on OKX.
2. Fix the cause: for example, close an unknown position by hand, or wait out a weekly loss.
3. Resume, in a terminal (agents cannot do this):
   ```bash
   .venv/bin/python -m ops.session --resume
   ```

A daily loss of 3R is not a halt: it only blocks new trades for the rest of the UTC day.

## Every week

```bash
.venv/bin/python -m harness.canaries     # harness self-test: four times "pass"
.venv/bin/python -m llm.funnel --audit   # how many relevant news items the filter discarded
docker compose ps                        # archives healthy
```

## Research: from idea to trade

Every step is a pull request. You approve by merging.

1. **Idea:** an issue `H-###` with the hypothesis and its economic reason.
2. **Spec:** `strategies/H-###/spec.yaml` plus `strategy.py`. Before the PR:
   `python -m harness.precheck strategies/H-### param=value`. A break-even win rate far above the
   no-cost one means the design cannot win. **Merging registers it.**
3. **Trials:** `python -m harness.run strategies/H-### param=value` for each variant. Every run is
   logged, failures too. The reports go in a PR.
4. **Verifier:** an independent review (Gemini: `agy`, see `docs/reviews/`). If it passes, add the
   trial id to `registry/approvals.yaml` by PR.
5. **Holdout, you only, once per hypothesis:**
   ```bash
   .venv/bin/python -m ingestion.binance_history --holdout
   .venv/bin/python -m harness.gatekeeper strategies/H-### param=value
   ```
6. **Paper:** add the strategy with `stage: paper` to `config/limits.yaml` by PR. Run it for at least
   3 months and 100 trades. The session report compares live R per trade with the backtest.
7. **Small live, then live:** `stage: live_small` (0.25R for 30 trades, then 0.5R), later
   `stage: live`. Each step is a PR.

## Money and tax

- Record deposits and withdrawals: `python -m ops.session --cashflow 5000 "deposit"`.
- Tax file per year, live trades only, EUR at the ECB rate of the closing day:
  `python -m ops.tax 2026` writes `data/reports/tax-2026.csv`. Give it to the Steuerberater (D6).

## What the system will not do

- **Promise profit.** It trades only what passed the gates. Today no strategy has.
- **Trade between sessions,** or without your approval of each trade card. Full-auto is deferred (D19).
- **Let an agent do lead-only actions:** switch to live, raise limits, approve strategies, resume
  after a halt, record cash flows or read the holdout.

## Troubleshooting

| Message | Meaning and fix |
|---|---|
| `config/limits.yaml differs from origin/main` | The local file was edited. `git checkout config/limits.yaml && git pull`. |
| `clock is off by ... ms` | Turn on automatic time in macOS settings. |
| `another session is running` | Wait, or if a session crashed, delete `data/session.lock`. |
| `data not fresh` | Binance data is late or unreachable. Run the session again in a few minutes. |
| `... is not merged on origin/main as-is` | The strategy files differ from their registered version. `git pull`, or register the change by PR. |
| `LLM extraction skipped` | Claude Code is missing or not the pinned version (`config/llm.yaml`). Trading goes on without new events. |
| `not enough free margin` | Too little USD margin on OKX for this trade. |
