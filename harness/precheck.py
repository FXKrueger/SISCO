"""Cost precheck for a draft spec, before registration.

  python -m harness.precheck strategies/H-002 param=value ...

Generates the signals only: no fills, no PnL, so it is not a trial and uses no budget. Reports
what a trade costs in R (cost in % / stop distance in %) and the win rate needed to break even
before any edge. A spec whose break-even win rate is out of reach cannot win by construction
(H-001 lesson). Put the output in the registration PR.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import costs, engine
from .config import SESSIONS_UTC
from .pit import Panel
from .run import lint, load_strategy, parse_value, strategy_files


def main(folder, params):
    folder = Path(folder)
    spec = yaml.safe_load((folder / "spec.yaml").read_text())
    lint("\n".join(f.read_text() for f in strategy_files(folder) if f.suffix == ".py"))
    panel = Panel.load(top_n=spec["universe"].get("top_n", 30))
    signals = engine.generate(load_strategy(folder)(params), panel, panel.sessions(SESSIONS_UTC))
    rows = []
    for t, s in signals:
        ref = s.entry.price if s.entry.kind == "limit" else engine.last_close(panel, s.coin, t)
        if not ref:
            continue
        stop_pct = abs(ref - s.stop) / ref
        adv = panel.adv(s.coin, t)
        # Round trip, exit at the stop (the most common exit), no bar-range slippage.
        rows.append({"stop_pct": stop_pct, "reward_R": abs(s.target - ref) / abs(ref - s.stop),
                     "cost_R": (costs.entry_cost(s.entry.kind, adv) + costs.exit_cost("stop", adv)) / stop_pct})
    d = pd.DataFrame(rows)
    if d.empty:
        print("no signals")
        return
    years = (panel.timeline()[-1] - panel.timeline()[0]).days / 365
    print(f"{spec['id']} {params}: {len(d)} signals, {len(d) / years:.0f} per year (before position limits)")
    print(f"stop distance: median {d.stop_pct.median():.2%}, 10th pct {d.stop_pct.quantile(0.1):.2%}")
    for mult in (1, 2):
        c = d.cost_R * mult
        be = (1 + c) / (d.reward_R + 1)  # win rate where p*reward - (1-p)*1 - cost = 0
        print(f"{mult}x costs: cost per trade median {c.median():.3f} R, mean {c.mean():.3f} R; "
              f"break-even win rate median {be.median():.1%} (no-cost break-even {(1 / (d.reward_R + 1)).median():.1%})")
    worst = d.cost_R.quantile(0.9) * 2
    if worst > 0.25:
        print(f"WARNING: 10% of trades cost more than {worst:.2f} R at 2x costs. Widen stops or use more liquid coins.")


if __name__ == "__main__":
    main(sys.argv[1], {k: parse_value(v) for k, v in (a.split("=", 1) for a in sys.argv[2:])})
