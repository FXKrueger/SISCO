"""Per-trade tax export (SPEC 13, D6). Live trades only; paper and demo are not taxable events.

  python -m ops.tax 2026        writes data/reports/tax-2026.csv

EUR amounts use the ECB euro reference rate of the closing day (the last published rate before
it on weekends and holidays). Tax treatment is to be confirmed with a Steuerberater (D6); this
file is the raw material, not a tax assessment.
"""

import csv
import io
import sys
import urllib.request
from pathlib import Path

import pandas as pd

from .journal import DB, Journal

ECB = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?format=csvdata&startPeriod={}&endPeriod={}"


def ecb_rates(start, end):
    """USD per EUR by date."""
    with urllib.request.urlopen(ECB.format(start, end), timeout=30) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode())))
    return pd.Series({pd.Timestamp(x["TIME_PERIOD"]): float(x["OBS_VALUE"]) for x in rows}).sort_index()


def export(year, journal=None, rates=None):
    j = journal or Journal()
    trades = [t for t in j.trades("mode = 'live' AND status = 'closed'") if t["closed_at"][:4] == str(year)]
    rates = rates if rates is not None else ecb_rates(f"{year - 1}-12-15", f"{year}-12-31")
    rows = []
    for t in trades:
        day = pd.Timestamp(t["closed_at"][:10])
        usd_per_eur = rates[rates.index <= day].iloc[-1]
        net = t["pnl_usd"] - (t["fee_usd"] or 0) + (t["funding_usd"] or 0)
        rows.append({"opened_utc": t["filled_at"], "closed_utc": t["closed_at"], "instrument": t["inst_id"], "side": t["side"],
                     "contracts": t["contracts"], "quantity": t["contracts"] * t["ct_val"], "entry_price_usd": t["entry_px"],
                     "exit_price_usd": t["exit_px"], "price_pnl_usd": round(t["pnl_usd"], 2), "fees_usd": round(t["fee_usd"] or 0, 2),
                     "funding_usd": round(t["funding_usd"] or 0, 2), "net_pnl_usd": round(net, 2), "ecb_usd_per_eur": usd_per_eur,
                     "net_pnl_eur": round(net / usd_per_eur, 2), "strategy": t["strategy"], "trade_id": t["id"]})
    out = Path(DB).parent / "reports" / f"tax-{year}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    return out, rows


if __name__ == "__main__":
    path, rows = export(int(sys.argv[1]))
    print(f"{len(rows)} closed live trades, net {sum(r['net_pnl_eur'] for r in rows):.2f} EUR -> {path}")
