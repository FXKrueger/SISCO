"""Gatekeeper (SPEC 6.4 gate 5, SPEC 12.1): the one holdout run per hypothesis. Protected.

  python -m harness.gatekeeper strategies/H-002 coins=3 lookback_d=30

Run by the lead only. Agents never run it and never read holdout data (CLAUDE.md rule 2).
Preconditions, all checked:
- the hypothesis is registered (files unchanged on origin/main),
- a development trial with exactly these params passed stage 3 (verdict "pass"),
- that trial is approved by the verifier: listed in registry/approvals.yaml on origin/main,
- the hypothesis has no holdout run yet. There is never a second one.
Holdout data (python -m ingestion.binance_history --holdout) lives in data/holdout, apart from
the development store. Signals before HOLDOUT_START only warm up indicators; only trades
entered inside the holdout count. Same gates as development, at 2x costs.
"""

import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

from . import engine, registry
from .config import HOLDOUT_START, SESSIONS_UTC
from .pit import STORE, Panel
from .run import GATES, Refused, check_registered, evaluate, final, gates, lint, load_strategy, parse_value, report, sha, strategy_files

HOLDOUT_STORE = STORE.parents[1] / "holdout" / "binance_um"


def approvals():
    r = subprocess.run(["git", "-C", str(Path(__file__).resolve().parents[1]), "show", "origin/main:registry/approvals.yaml"],
                       capture_output=True, text=True)
    return {t for item in (yaml.safe_load(r.stdout) or []) for t in item["trials"]} if r.returncode == 0 else set()


def preconditions(hyp, params):
    """The development trial that qualifies this holdout run, or Refused."""
    bad = registry.invalidated()
    entries = registry.entries()
    if any(e["hypothesis"] == hyp and e["kind"] in ("holdout", "holdout_start") for e in entries):  # an aborted run counts too
        raise Refused(f"{hyp} already had its holdout run. There is never a second one.")
    passed = [e for e in entries if e["hypothesis"] == hyp and e["kind"] == "result" and e["params"] == params
              and e.get("verdict") == "pass" and e["trial_id"] not in bad]
    if not passed:
        raise Refused(f"no development trial of {hyp} with params {params} passed stage 3")
    dsr, pbo = final(hyp)  # deflated by every trial of the hypothesis, not only those before it
    passed = [e for e in passed if dsr.get(e["trial_id"], 0) >= GATES["dsr"] and (pbo is None or pbo <= GATES["pbo"])]
    if not passed:
        raise Refused(f"no trial of {hyp} with params {params} passes DSR >= {GATES['dsr']} and PBO <= {GATES['pbo']} "
                      "when deflated by all trials of the hypothesis")
    ok = approvals()
    approved = [e for e in passed if e["trial_id"] in ok]
    if not approved:
        raise Refused(f"trial {passed[-1]['trial_id']} is not approved by the verifier (registry/approvals.yaml on origin/main)")
    return approved[-1]


def main(folder, params, holdout_store=HOLDOUT_STORE):
    folder = Path(folder)
    spec = yaml.safe_load((folder / "spec.yaml").read_text())
    hyp = spec["id"]
    check_registered(folder)
    lint("\n".join(f.read_text() for f in strategy_files(folder) if f.suffix == ".py"))
    dev_trial = preconditions(hyp, params)
    if not (holdout_store / "klines_1h").exists():
        raise Refused(f"no holdout data in {holdout_store}. The lead runs: python -m ingestion.binance_history --holdout")
    from .config import HOLDOUT_END

    record = {"hypothesis": hyp, "params": params, "spec_sha256": sha((folder / "spec.yaml").read_bytes()),
              "dev_trial": dev_trial["trial_id"], "holdout": [HOLDOUT_START.isoformat(), HOLDOUT_END.isoformat()]}
    start_id = registry.append({"kind": "holdout_start", **record})  # logged before anything can fail
    panel = Panel.load(top_n=spec["universe"].get("top_n", 30), stores=(STORE, holdout_store), end=HOLDOUT_END,
                       start=HOLDOUT_START - pd.Timedelta(days=150))
    times = panel.sessions(SESSIONS_UTC)
    signals = [(t, s) for t, s in engine.generate(load_strategy(folder)(params), panel, times) if t >= HOLDOUT_START]
    bt = [engine.backtest(signals, panel, m, sessions=SESSIONS_UTC) for m in (1.0, 2.0)]
    r, series = evaluate(bt[0], bt[1], panel, hyp, HOLDOUT_START)
    g, verdict = gates(r)
    trial_id = registry.append({"kind": "holdout", "start": start_id, **record, "verdict": verdict, "gates": g, "results": r}, series)
    path = report(folder, spec, trial_id, params, r, g, verdict)
    print(f"HOLDOUT {trial_id} {verdict}: {path}")
    return verdict


if __name__ == "__main__":
    try:
        main(sys.argv[1], {k: parse_value(v) for k, v in (a.split("=", 1) for a in sys.argv[2:])})
    except Refused as e:
        sys.exit(f"REFUSED: {e}")
