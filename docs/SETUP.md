# SISCO setup guide

Step by step, from an empty Mac to a system that trades. Each stage ends with a check. Do not skip
a stage: paper before demo, demo before live.

Commands run in the repo folder unless noted. `$REPO` is where you cloned SISCO.

## Stage 1: software (once)

1. Install the tools:
   ```bash
   brew install python@3.14 git gh colima docker docker-compose
   ```
2. Update Claude Code to 2.1.280 or newer (the pinned Opus model needs it):
   ```bash
   claude update
   ```
3. Clone and install:
   ```bash
   gh repo clone FXKrueger/SISCO && cd SISCO
   python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
4. Check: `.venv/bin/python -m pytest -q tests` shows all tests passing.

## Stage 2: day-1 archives (once, then always on while the Mac is on)

1. Start Docker and the archiver, and let Colima start at login:
   ```bash
   colima start && docker compose up -d --build && brew services start colima
   ```
2. Optional, so the Mac does not sleep on the charger:
   ```bash
   sudo pmset -c sleep 0 disksleep 0
   ```
3. Turn on Time Machine with an external disk. It is the only backup of `data/` (D19).
4. Check: `docker compose ps` shows `healthy` after two minutes.

## Stage 3: research data (once, about 3 hours)

1. Development history (never the holdout):
   ```bash
   .venv/bin/python -m ingestion.binance_history
   .venv/bin/python -m ingestion.binance_history --metrics
   ```
2. Check: `.venv/bin/python -m harness.canaries` prints `pass` four times.

## Stage 4: paper trading (no exchange account needed)

1. Set the paper account's starting equity (USD). It stays in your local journal, never in the repo:
   ```bash
   .venv/bin/python -m ops.init --paper-equity 10000
   ```
2. Check: `.venv/bin/python -m ops.init` ends with `ready`.
3. A strategy trades only after you approve it in `config/limits.yaml` **by pull request**. The
   session reads this file only as merged on `origin/main`, so a local edit has no effect.
   Per SPEC 6.4, approve for paper only strategies that passed the gatekeeper (holdout). Example
   entry:
   ```yaml
   strategies:
     - id: H-002
       params: {coins: 3, lookback_d: 30}
       stage: paper
   ```
4. Run the first session as a dry run, then for real:
   ```bash
   .venv/bin/python -m ops.session --dry-run
   .venv/bin/python -m ops.session
   ```

## Stage 5: OKX demo trading (tests the real API without real money)

1. On OKX (EEA): switch to **Demo trading**, create an API key with permissions **Read** and
   **Trade** only. Never **Withdraw**. Bind it to your IP address if you can.
2. Store the key outside the repo, readable only by you:
   ```bash
   mkdir -p ~/.sisco && touch ~/.sisco/okx.env && chmod 600 ~/.sisco/okx.env
   ```
   Then put these three lines into `~/.sisco/okx.env` with a text editor:
   ```
   SISCO_OKX_KEY=...
   SISCO_OKX_SECRET=...
   SISCO_OKX_PASSPHRASE=...
   ```
3. Switch the mode to `demo` in `config/limits.yaml` by pull request, merge it, then `git pull`.
4. Check: `.venv/bin/python -m ops.init` shows `OKX demo account reachable`.
5. Test the full order cycle on demo before anything else. The order code has only been tested
   against a simulated exchange so far (docs/reviews/2026-09-24-gemini-2.md, "before live mode"):
   - run a dry run, then one session that places one trade;
   - compare with the OKX demo website: the position, and the stop and target orders attached to it;
   - after the trade closes (stop, target or time limit), run a session and check that the report
     and `.venv/bin/python -m ops.tax <year>` match OKX. Demo trades do not go into the tax file,
     so compare the journal instead: `sqlite3 data/journal.db "select * from trades"`.

## Stage 6: live (real money)

Only when all of these are true:
- a strategy passed stage 3, the verifier review, the holdout and at least 3 months and 100 trades
  of paper trading within its backtest range (SPEC 6.4);
- stage 5 worked end to end;
- X-Perps fee tiers are confirmed (SPEC 15).

Then:
1. Create a **live** API key: Read and Trade only, never Withdraw, IP-bound. Replace the demo
   lines in `~/.sisco/okx.env`.
2. Keep only the margin needed plus a buffer on the exchange (SPEC 10).
3. By pull request: `mode: live`, and the strategy's `stage: live_small` (0.25R for 30 trades,
   then 0.5R). Promote to `stage: live` later, by another pull request.
4. Record every deposit or withdrawal, otherwise it looks like profit or a drawdown:
   ```bash
   .venv/bin/python -m ops.session --cashflow -2000 "withdrawal to bank"
   ```

## Stage 7: LLM events (phase B)

1. After `claude update`, set `cli_version` in `config/llm.yaml` to the output of
   `claude --version` by pull request (SPEC 8.4: a new version is a model change).
2. Check: `.venv/bin/python -m llm.funnel --dry-run` lists story clusters. Sessions then extract
   up to 10 new stories each.

## Stage 8: GitHub hygiene

1. Give agents their own GitHub account with write access (not admin), then remove the admin
   bypass from `.github/rulesets/main.json` (issue #3).
2. After every guard update: `.venv/bin/python -m pytest -q tests/test_guard.py`.
