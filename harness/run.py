"""Run one trial of a registered hypothesis.

  python -m harness.run strategies/H-001 levels=fib lookback_h=240

Refuses to run unless spec.yaml and strategy.py are merged on origin/main unchanged
(pre-registration), the params are in the spec's grid, the trial budget is not used up and the
strategy code passes the lint. Every started trial is logged, including ones that crash.
"""

import hashlib
import importlib.util
import re
import subprocess
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import engine, registry, stats
from .config import CRASHES, DELAYS_H, DEV_END, REGIMES, SESSIONS_UTC, TRIAL_BUDGET_DEFAULT
from .pit import Panel

GATES = {"dsr": 0.95, "pbo": 0.2}
TREND_BASELINE = "B-001"
# Strategy code reads data only through PITView and never calls an LLM (CLAUDE.md rules 5 and 6).
# ponytail: a token lint, not a sandbox. The verifier's code review is the second line.
FORBIDDEN = [r"\bopen\(", r"\bimport os\b", r"\bfrom os\b", r"duckdb", r"read_parquet", r"read_csv", r"urllib", r"requests",
             r"socket", r"subprocess", r"anthropic", r"claude", r"\._", r"__dict__", r"\bglobals\(", r"\bgetattr\(",
             r"\binspect\b", r"\bgc\b", r"\bPanel\b", r"harness\.(engine|registry|run|stats|costs)", r"\beval\(", r"\bexec\("]


class Refused(Exception):
    pass


def sha(b):
    return hashlib.sha256(b).hexdigest()


def check_registered(folder):
    subprocess.run(["git", "fetch", "-q", "origin", "main"], check=True)
    for name in ("spec.yaml", "strategy.py"):
        path = (folder / name).as_posix()
        merged = subprocess.run(["git", "show", f"origin/main:{path}"], capture_output=True)
        if merged.returncode or merged.stdout != (folder / name).read_bytes():
            raise Refused(f"{path} is not merged on origin/main as-is. Register it by PR first.")


def lint(src):
    hits = [p for p in FORBIDDEN if re.search(p, src)]
    if hits:
        raise Refused(f"strategy code uses forbidden patterns {hits}. Read data only through the PITView.")


def load_strategy(folder):
    spec = importlib.util.spec_from_file_location(f"strategy_{folder.name}", folder / "strategy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Strategy


def parse_value(v):
    return yaml.safe_load(v)


def evaluate(trades_1x, trades_2x, panel, hypothesis, start):
    d1 = stats.daily(trades_1x, start, DEV_END)
    d2 = stats.daily(trades_2x, start, DEV_END)
    prior = registry.series(hypothesis)
    matrix = pd.concat([prior, d2.rename("this")], axis=1).fillna(0.0)
    srs = [x.mean() / x.std(ddof=1) for _, x in matrix.items() if x.std(ddof=1) > 0]
    btc = panel.bars.get("BTC")
    bh = None
    if btc is not None:
        px = btc.set_index("event_time").close
        r = np.log(px).diff().groupby(px.index.floor("D")).sum()
        bh = stats.sharpe(r[(r.index >= pd.Timestamp(start).floor("D"))])
    return {
        "trades": int(len(trades_2x)),
        "win_rate": float((trades_2x.net_R > 0).mean()) if len(trades_2x) else None,
        "net_R_1x": float(d1.sum()), "net_R_2x": float(d2.sum()),
        "sharpe_1x": stats.sharpe(d1), "sharpe_2x": stats.sharpe(d2),
        "max_dd_R_2x": stats.max_drawdown(d2.to_numpy()),
        "dsr": stats.deflated_sharpe(d2.to_numpy(), srs),
        "pbo": stats.pbo(matrix.to_numpy()),
        "trials_in_hypothesis": int(matrix.shape[1]),
        "btc_buy_hold_sharpe": bh,
        "trend_baseline_sharpe": baseline_sharpe(hypothesis),
        "null": stats.null_test(trades_2x, panel),
        "regimes": stats.by_window(trades_2x, REGIMES),
        "crashes": stats.by_window(trades_2x, CRASHES),
        "skipped_signals": trades_2x.attrs.get("skipped_signals", 0),
    }, {"net_1x": d1, "net_2x": d2}


def baseline_sharpe(hypothesis):
    """2x-cost Sharpe of the latest registered trend-baseline result, None before it has run."""
    if hypothesis == TREND_BASELINE:
        return None
    bad = registry.invalidated()
    runs = [e for e in registry.entries() if e["hypothesis"] == TREND_BASELINE and e["kind"] == "result" and e["trial_id"] not in bad]
    return runs[-1]["results"]["sharpe_2x"] if runs else None


def gates(r):
    g = {
        "beats_btc_buy_hold": r["btc_buy_hold_sharpe"] is not None and r["sharpe_2x"] > r["btc_buy_hold_sharpe"],
        "beats_trend_baseline": None if r.get("trend_baseline_sharpe") is None else r["sharpe_2x"] > r["trend_baseline_sharpe"],
        f"dsr>={GATES['dsr']}": r["dsr"] >= GATES["dsr"],
        f"pbo<={GATES['pbo']}": None if r["pbo"] is None else r["pbo"] <= GATES["pbo"],
        "beats_null_p95": None if r["null"] is None else r["null"]["passed"],
        "positive_in_every_regime": all(v["net_R"] > 0 for v in r["regimes"].values() if v["trades"]),
    }
    decided = [v for v in g.values() if v is not None]
    verdict = "fail" if not all(decided) else ("pass" if len(decided) == len(g) else "inconclusive")
    return g, verdict


def fmt(x, nd=2):
    return "n/a" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def report(folder, spec, trial_id, params, r, g, verdict):
    flags = []
    if r["trades"] < 100:
        flags.append(f"only {r['trades']} trades")
    if r["skipped_signals"]:
        flags.append(f"{r['skipped_signals']} signals skipped (position or 5R limit)")
    if r["net_R_1x"] > 0 >= r["net_R_2x"]:
        flags.append("edge disappears at 2x costs")
    late = r.get("delay_net_R_2x", {})
    if late and r["net_R_2x"] > 0 and min(late.values()) <= 0:
        flags.append("edge disappears with slower reaction: it needs faster execution than sessions")
    flags.append("cost model v1: X-Perps spread and fees not yet calibrated")
    reg = "\n".join(f"| {k} | {v['trades']} | {v['net_R']:.1f} | {fmt(v['win_rate'])} |" for k, v in r["regimes"].items())
    crash = "\n".join(f"| {k} | {v['trades']} | {v['net_R']:.1f} |" for k, v in r["crashes"].items())
    gate = "\n".join(f"- {k}: {'n/a' if v is None else ('yes' if v else 'NO')}" for k, v in g.items())
    null = r["null"] or {}
    text = f"""# {spec['id']} trial {trial_id}: {verdict.upper()}

{spec['title']}. Params: `{params}`. Development data up to {DEV_END.date()}. Costs 2x unless noted.

## Key numbers

| | 1x costs | 2x costs |
|---|---|---|
| Net R | {r['net_R_1x']:.1f} | {r['net_R_2x']:.1f} |
| Net Sharpe (daily, annualized) | {r['sharpe_1x']:.2f} | {r['sharpe_2x']:.2f} |

- DSR: {fmt(r['dsr'])} (trials in hypothesis: {r['trials_in_hypothesis']})
- PBO: {fmt(r['pbo'])}
- Max drawdown: {r['max_dd_R_2x']:.1f} R
- Trades: {r['trades']}, win rate {fmt(r['win_rate'])}
- BTC buy-and-hold Sharpe, same period: {fmt(r['btc_buy_hold_sharpe'])}; trend baseline (B-001) Sharpe at 2x: {fmt(r.get('trend_baseline_sharpe'))}
- Reaction delay, net R at 2x costs: {', '.join(f"+{h} h: {v:.1f}" for h, v in r.get('delay_net_R_2x', {}).items()) or 'n/a'}
- Decisions at sessions {SESSIONS_UTC} UTC only; time exits and order expiries wait for the next session (D19)
- Null test (1000 random entries): strategy beats {fmt(null.get('beats_share'))} of runs, 95th pct {fmt(null.get('p95'), 1)} R

## Gates (stage 3)

{gate}

## Regime split

| Regime | Trades | Net R | Win rate |
|---|---|---|---|
{reg}

## Crash stress

| Window | Trades | Net R |
|---|---|---|
{crash}

Calibration: n/a until the meta-model exists (M2).

## Red flags

{chr(10).join('- ' + f for f in flags)}

## Proposed next step

(filled in by the research agent)
"""
    out = folder / "reports" / f"{trial_id}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text)
    return out


def main(folder, params):
    folder = Path(folder)
    spec = yaml.safe_load((folder / "spec.yaml").read_text())
    hyp = spec["id"]
    check_registered(folder)
    for k, v in params.items():
        if k not in spec["params"] or v not in spec["params"][k]:
            raise Refused(f"{k}={v} is not in the registered grid {spec['params'].get(k)}")
    missing = set(spec["params"]) - set(params)
    if missing:
        raise Refused(f"missing params {sorted(missing)}")
    budget = spec.get("trial_budget", TRIAL_BUDGET_DEFAULT)
    bad = registry.invalidated()
    used = sum(e["hypothesis"] == hyp and e["kind"] == "start" and e["trial_id"] not in bad for e in registry.entries())
    if used >= budget:
        raise Refused(f"trial budget used up ({used}/{budget}). The hypothesis is closed.")
    src = (folder / "strategy.py").read_text()
    lint(src)
    record = {"hypothesis": hyp, "params": params, "spec_sha256": sha((folder / "spec.yaml").read_bytes()),
              "code_sha256": sha(src.encode()), "dev_end": DEV_END.isoformat()}
    start_id = registry.append({"kind": "start", **record})
    try:
        uni = spec["universe"]
        panel = Panel.load(top_n=uni.get("top_n", 30))
        signals = engine.generate(load_strategy(folder)(params), panel, panel.sessions(SESSIONS_UTC))

        def bt(cost_mult, delay_h=0):
            return engine.backtest(signals, panel, cost_mult, sessions=SESSIONS_UTC, delay_h=delay_h)

        t1, t2 = bt(1.0), bt(2.0)
        start = panel.timeline()[0]
        r, series = evaluate(t1, t2, panel, hyp, start)
        r["delay_net_R_2x"] = {h: float(x.net_R.sum()) if len(x := bt(2.0, h)) else 0.0 for h in DELAYS_H}
        g, verdict = gates(r)
    except Exception as e:
        registry.append({"kind": "error", "start": start_id, **record, "error": traceback.format_exc(limit=3)})
        raise
    trial_id = registry.append({"kind": "result", "start": start_id, **record, "verdict": verdict, "gates": g, "results": r}, series)
    path = report(folder, spec, trial_id, params, r, g, verdict)
    print(f"{trial_id} {verdict}: {path}")


if __name__ == "__main__":
    try:
        main(sys.argv[1], {k: parse_value(v) for k, v in (a.split("=", 1) for a in sys.argv[2:])})
    except Refused as e:
        sys.exit(f"REFUSED: {e}")
