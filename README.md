# SISCO

Personal trading intelligence system for crypto perpetual futures. It combines quantitative
market data (price, derivatives positioning, liquidity, on-chain) with LLM-extracted context
(news, events, narratives). A strict validation harness decides what gets traded.

- [System specification](docs/SPEC.md)
- [Decision log](docs/DECISIONS.md)
- [Agent guide](CLAUDE.md)

Status: M0 done (archives run on the lead's Mac until the VPS exists, issue #1). M1 harness in review.

## Day-1 archives (`ingestion/`)

Raw, append-only, hourly zstd JSONL under `data/raw/<source>/<date>/<hour>.jsonl.zst`:

- `okx_xperps`: every live X-Perp on `eea.okx.com`: full order book, trades, funding, open interest.
- `binance_liquidations`, `okx_liquidations`, `okx_xperps_liquidations`.
- `bluesky`: all posts (Jetstream).
- `text/*`: exchange announcements and listings, crypto news, regulators, Reddit.

Volume measured 2026-09-24: about 3 GB/day for X-Perps and 1.2 GB/day for Bluesky, compressed.

```bash
docker compose up -d --build
```

`docker compose ps` shows `unhealthy` when any source is silent for 10 minutes.
Tests: `python -m pytest -q tests`.

## Harness (`harness/`, protected)

```bash
python -m ingestion.binance_history     # development data into data/store (never the holdout)
python -m harness.canaries              # self-tests: leak, random, known effect
python -m harness.run strategies/H-001 levels=fib lookback_h=240
```

`harness.run` refuses a hypothesis whose `spec.yaml` and `strategy.py` are not merged on
`origin/main` unchanged, params outside the registered grid, a used-up trial budget and strategy
code that reaches past the `PITView`. Every started trial goes into the hash-chained registry
(`data/registry/`), including crashes. The one-page report lands in `strategies/H-###/reports/`.

## Protected paths

`harness/`, `risk/`, `execution/`, `registry/`, `config/limits.yaml` and the guardrails
themselves (`.github/`, `.claude/`, `CLAUDE.md`) need the lead's review. Two layers:

1. Server side (the real one): `.github/CODEOWNERS` plus the ruleset in
   `.github/rulesets/main.json` (PR required, code owner review for the paths above, no force
   push, no deletion). Repo admins may merge a PR past code owner review, but never push
   directly. That bypass exists because agents still push with the lead's account, and GitHub
   does not let an author approve their own PR. Next step: give agents their own GitHub account
   (write access, not admin), then remove the bypass. Until then, only layer 2 stops an agent
   from merging protected changes.
2. Local, for Claude Code agents in this repo: `.claude/hooks/guard.py` blocks merging PRs
   that touch protected paths, pushing to main, GitHub API writes to merges, refs, contents,
   rulesets and settings, and edits to the guard itself. It catches drift, not deliberate evasion.
