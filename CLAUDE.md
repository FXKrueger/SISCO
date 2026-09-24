# SISCO agent guide

SISCO is a personal trading system for crypto perpetual futures. A human lead sets direction
and approves. Agents do the research, building, validation and testing, and report results.

Read first:

- `docs/SPEC.md`: what the system is and how it works.
- `docs/DECISIONS.md`: why. Do not reopen a decided question without new evidence.

## Hard rules

1. Never change `harness/`, `risk/`, `execution/`, `config/limits.yaml` or the cost model
   without a pull request that the lead approves.
2. Never read, copy or ask for holdout data.
3. Performance claims come only from harness runs of registered specs. Exploratory analysis on
   development data is fine. Evaluating how a strategy variant performs is a trial, and it must
   go through the harness.
4. Never skip, hide or delete a trial. Report failures in the same format as successes.
5. Strategy code reads data only through `PITView`. No direct file or API access.
6. LLM features come only from the cached extraction pipeline. No LLM calls in strategy code.
7. You may halt trading. You may never resume trading, raise limits or switch a strategy to full-auto.
8. Never tune a strategy on holdout, paper trading or live results.
9. If you are unsure whether something leaks future information, assume it does and flag it.

## Reporting to the lead

- Use the one-page template in `docs/SPEC.md` section 12.4.
- Numbers first, verdict clear, red flags explicit.
- Plain, simple language. No hype, no em-dashes.

## Conventions

- Python. Data in Parquet, queried with DuckDB.
- One folder per hypothesis: `strategies/H-###/` with `spec.yaml`, code and report.
- Every data row carries `event_time`, `available_time`, `ingested_at`, `source`, `revision`.
