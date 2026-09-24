"""A trading session (D19, SPEC 11). The system only acts while this runs.

  python -m ops.session              run a session
  python -m ops.session --dry-run    everything except placing orders
  python -m ops.session --resume     clear a kill-switch halt (the lead only, CLAUDE.md rule 7)

Order of steps: lock, clock check, reconciliation, loss limits and kill switch, housekeeping
(time exits, stale entries), fresh data, signals from approved strategies only, risk check,
trade cards for the lead, orders, report.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from execution import okx
from execution.paper import PaperBroker
from harness import canaries
from harness.config import SESSIONS_UTC
from harness.engine import next_session
from harness.run import Refused, check_registered, lint, load_strategy, strategy_files
from risk.engine import Account, Position, Proposal, check, halt_reason, load_limits

from . import live_data
from .journal import Journal, now

DATA = Path(os.environ.get("SISCO_DATA", "data"))
ENV_FILE = Path.home() / ".sisco" / "okx.env"
ROOT = Path(__file__).parents[1]


def load_env():
    if not ENV_FILE.exists():
        return
    if ENV_FILE.stat().st_mode & 0o077:
        sys.exit(f"{ENV_FILE} must be readable by you only: chmod 600 {ENV_FILE}")
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def session_time(t):
    """The latest session time at or before t. Decisions use data known at that time only."""
    t = t.floor("h")
    while t.hour not in SESSIONS_UTC:
        t -= pd.Timedelta(hours=1)
    return t


def stage_mult(journal, strategy, stage):
    """SPEC 6.4: paper and live at full size; live_small at 0.25R for 30 trades, then 0.5R."""
    if stage != "live_small":
        return 1.0
    n = len(journal.trades("strategy = ? AND mode != 'paper' AND status = 'closed'", (strategy,)))
    return 0.25 if n < 30 else 0.5


def pnl_R(journal, mode, broker, one_r_usd, since):
    closed = sum(journal.r_multiple(t) * t["one_r_usd"] for t in journal.trades("mode = ? AND status = 'closed' AND closed_at >= ?", (mode, since.isoformat())))
    upl = sum(p["upl"] for p in broker.positions())
    return (closed + upl) / one_r_usd if one_r_usd else 0.0


def reconcile(broker, journal):
    """Live and demo: bring the journal in line with the exchange. Returns problems (kill switch)."""
    problems = []
    pos = {p["instId"]: p for p in broker.positions()}
    pend = {o["clOrdId"] for o in broker.pending()}
    mine = journal.trades("mode = ? AND status IN ('pending', 'open')", (broker.mode,))
    for t in mine:
        if t["status"] == "pending" and t["id"] in pend:
            continue
        if t["inst_id"] in pos:
            p = pos[t["inst_id"]]
            if t["status"] == "pending":
                journal.update(t["id"], status="open", entry_px=p["avg_px"], filled_at=now())
            if abs(p["contracts"] - t["contracts"]) > 1e-9:
                problems.append(f"{t['inst_id']}: exchange has {p['contracts']} contracts, journal {t['contracts']}")
            if not broker.protected(t["inst_id"]):
                problems.append(f"{t['inst_id']}: open position without a stop at the exchange")
            continue
        since = int(pd.Timestamp(t["placed_at"]).timestamp() * 1000)
        closed = [c for c in broker.closed_positions(since) if c["instId"] == t["inst_id"]]
        if closed:
            c = closed[0]
            s = 1 if t["side"] == "long" else -1
            reason = "target" if s * (c["close_px"] - t["target"]) >= 0 else ("stop" if s * (c["close_px"] - t["stop"]) <= 0 else "other")
            journal.update(t["id"], status="closed", exit_px=c["close_px"], exit_reason=reason, pnl_usd=c["pnl"], fee_usd=c["fee"],
                           funding_usd=c["funding"], closed_at=datetime.fromtimestamp(c["closed_ms"] / 1000, timezone.utc).isoformat(timespec="seconds"),
                           entry_px=t["entry_px"] or t["planned_entry"])
        elif t["status"] == "pending":
            journal.update(t["id"], status="cancelled", closed_at=now())
        else:
            problems.append(f"{t['inst_id']}: position gone from the exchange and no close record found")
    known = {t["inst_id"] for t in journal.trades("mode = ? AND status = 'open'", (broker.mode,))}
    problems += [f"{i}: position at the exchange that the journal does not know" for i in pos if i not in known]
    return problems


def halt(journal, brokers, reason, report):
    """Kill switch: cancel open entries, keep stops, stop trading until the lead resumes."""
    journal.set("halted", reason)
    for b in brokers.values():
        for o in b.pending():
            try:
                b.cancel(o["instId"], o["clOrdId"])
            except okx.OkxError as e:
                report.append(f"- could not cancel {o['clOrdId']}: {e}")
    report.append(f"\n**HALTED: {reason}.** Open entries cancelled, stops kept. Only the lead can resume (`--resume`).")


def card(sig, strat, inst, prop, sized, mode, beta):
    s = 1 if prop.side == "long" else -1
    rr = abs(prop.target - prop.entry) / abs(prop.entry - prop.stop)
    return (f"[{mode}] {strat['id']} {prop.side.upper()} {prop.coin} ({inst['instId']})\n"
            f"  entry {sig.entry.kind} {prop.entry:.6g}   stop {prop.stop:.6g} ({abs(prop.entry - prop.stop) / prop.entry:.2%})"
            f"   target {prop.target:.6g} ({rr:.1f}R)   time limit {sig.time_limit}\n"
            f"  size {sized.contracts:g} contracts, risk {sized.risk_usd:.2f} USD = {sized.risk_R:.2f}R, "
            f"notional {sized.notional:.0f} USD, leverage {sized.leverage}x isolated, beta {beta:.2f}\n"
            f"  features {json.dumps({k: round(v, 4) for k, v in sig.features.items()})}")


def run(args):
    limits = load_limits()
    mode = limits["mode"]
    journal = Journal()
    report = [f"# Session {now()}  (mode: {mode})\n"]
    lock = DATA / "session.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        sys.exit(f"another session is running (or crashed): remove {lock} if you are sure it is not")
    try:
        os.close(fd)
        if args.resume:
            h = journal.get("halted")
            if not h:
                print("not halted")
                return
            print(f"Halted because: {h}")
            if input("Type RESUME to clear the halt: ").strip() != "RESUME":
                return
            journal.set("halted", None)
            journal.veto("-", "-", "-", "lead", f"resumed after: {h}", input("Reason for resuming: "))
            print("resumed")
            return

        drift = abs(okx.server_time_ms() - time.time() * 1000)
        if drift > 2000:
            sys.exit(f"clock is off by {drift:.0f} ms vs the exchange. Fix the system clock (NTP) first.")

        load_env()
        brokers = {"paper": PaperBroker(journal)}
        if mode in ("demo", "live"):
            brokers[mode] = okx.OkxBroker(demo=mode == "demo")
            brokers[mode].setup()
        live_broker = brokers.get(mode, brokers["paper"])

        # 1. Reconcile and advance the paper simulation.
        brokers["paper"].sync()
        problems = reconcile(live_broker, journal) if mode in ("demo", "live") else []

        # 2. Account state and kill switch (the real account in demo/live, the paper account in paper).
        t_now = pd.Timestamp.now(tz="UTC")
        acc = live_broker.account()
        peak = max(journal.get(f"peak_{live_broker.mode}", acc["equity"]), acc["equity"])
        journal.set(f"peak_{live_broker.mode}", peak)
        one_r = acc["equity"] * limits["risk_per_trade_pct"] / 100
        account = Account(acc["equity"], peak, acc["available"],
                          pnl_today_R=pnl_R(journal, live_broker.mode, live_broker, one_r, t_now.floor("D")),
                          pnl_7d_R=pnl_R(journal, live_broker.mode, live_broker, one_r, t_now - pd.Timedelta(days=7)),
                          halted=journal.get("halted"))
        report.append(f"Equity {acc['equity']:.2f} USD (peak {peak:.2f}), 1R = {one_r:.2f} USD, "
                      f"today {account.pnl_today_R:+.2f}R, 7 days {account.pnl_7d_R:+.2f}R\n")
        if live_broker.errors >= limits["max_consecutive_api_errors"]:
            problems.append(f"{live_broker.errors} consecutive API errors")
        reason = "; ".join(problems) if problems else halt_reason(account, limits)
        if reason and not journal.get("halted"):
            halt(journal, brokers, reason, report)
            account.halted = reason

        # 3. Housekeeping, also while halted: close positions past their time limit, cancel
        #    entries that were valid until this session.
        for t in journal.trades("status IN ('pending', 'open')"):
            b = brokers["paper"] if t["mode"] == "paper" else live_broker
            if t["status"] == "pending" and pd.Timestamp(t["placed_at"]) < session_time(t_now):
                b.cancel(t["inst_id"], t["id"])
                journal.update(t["id"], status="cancelled", closed_at=now())
                report.append(f"- cancelled unfilled entry {t['coin']} ({t['strategy']})")
            elif t["status"] == "open" and next_session(pd.Timestamp(t["filled_at"]) + pd.Timedelta(hours=t["time_limit_h"]), SESSIONS_UTC) <= t_now:
                b.close(t["inst_id"])
                report.append(f"- closed {t['coin']} ({t['strategy']}) at its time limit")
        if mode in ("demo", "live"):
            reconcile(live_broker, journal)
        brokers["paper"].sync()

        # 4. Signals, once per session time, from approved strategies only.
        t_sess = session_time(t_now)
        approved = [s for s in limits["strategies"] if s.get("stage") in ("paper", "live_small", "live")]
        if account.halted:
            report.append("No new trades: halted.")
        elif not approved:
            report.append("No approved strategies in config/limits.yaml. Nothing to trade.")
        elif journal.get("last_signal_time") == t_sess.isoformat():
            report.append(f"Signals for the {t_sess:%H:%M} UTC session were already handled.")
        elif account.pnl_today_R <= -limits["daily_loss_stop_R"]:
            report.append("Daily loss limit reached: no new trades today.")
        else:
            canaries.leak_canary()
            top_n = max(30, max(_spec(s["id"])["universe"].get("top_n", 30) for s in approved))
            panel = live_data.panel(t_sess, top_n=top_n)
            fresh = panel.bars["BTC"].available_time.max()
            if fresh < t_sess:
                sys.exit(f"data not fresh: last BTC bar known at {fresh}, session time {t_sess}")
            from harness.pit import PITView

            for strat in approved:
                folder = ROOT / "strategies" / strat["id"]
                try:
                    check_registered(folder)
                    lint("\n".join(f.read_text() for f in strategy_files(folder) if f.suffix == ".py"))
                except Refused as e:
                    report.append(f"- {strat['id']} skipped: {e}")
                    continue
                spec = _spec(strat["id"])
                sub = type(panel)({c: d for c, d in panel.bars.items()}, panel.funding, top_n=spec["universe"].get("top_n", 30))
                signals = load_strategy(folder)(strat["params"]).on_bar(t_sess, PITView(sub, t_sess)) or []
                b = brokers["paper"] if strat["stage"] == "paper" else live_broker
                for sig in signals:
                    _handle(sig, strat, b, sub, journal, limits, args, report, t_sess)
            journal.set("last_signal_time", t_sess.isoformat())
            if mode in ("demo", "live"):
                time.sleep(2)
                reconcile(live_broker, journal)  # record market fills now, so time limits start at the fill

        # 5. Report.
        report.append("\n## Open positions and entries\n")
        for t in journal.trades("status IN ('pending', 'open')"):
            report.append(f"- [{t['mode']}] {t['strategy']} {t['side']} {t['coin']} {t['status']}, stop {t['stop']:.6g}, target {t['target']:.6g}")
        report.append("\n## Strategies vs backtest\n")
        for strat in limits["strategies"]:
            closed = journal.trades("strategy = ? AND status = 'closed'", (strat["id"],))
            rs = [journal.r_multiple(t) for t in closed]
            report.append(f"- {strat['id']} ({strat.get('stage')}): {len(rs)} closed trades, "
                          f"{(sum(rs) / len(rs) if rs else 0):+.3f} R/trade{_expected(strat)}")
    finally:
        lock.unlink(missing_ok=True)
    text = "\n".join(report)
    out = DATA / "reports" / f"session-{datetime.now(timezone.utc):%Y%m%d-%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    journal.log_session(mode, text)
    print(text + f"\n\nReport saved: {out}")


def _spec(sid):
    import yaml

    return yaml.safe_load((ROOT / "strategies" / sid / "spec.yaml").read_text())


def _expected(strat):
    """Backtest expectation for this strategy and params from the registry (net R/trade at 2x costs)."""
    from harness import registry

    bad = registry.invalidated()
    runs = [e for e in registry.entries() if e["kind"] == "result" and e["hypothesis"] == strat["id"]
            and e["params"] == strat["params"] and e["trial_id"] not in bad]
    if not runs:
        return ", no backtest on record"
    r = runs[-1]["results"]
    return f", backtest {r['net_R_2x'] / max(r['trades'], 1):+.3f} R/trade at 2x costs ({runs[-1]['trial_id']})"


def _handle(sig, strat, broker, panel, journal, limits, args, report, t_sess):
    coin = live_data.base_coin(sig.coin)
    inst = broker.instruments.get(coin)
    if not inst:
        journal.veto(strat["id"], sig.coin, sig.side, "system", "not listed on X-Perps")
        report.append(f"- {strat['id']} {sig.side} {sig.coin}: not listed on X-Perps, skipped")
        return
    # Levels come from Binance prices; translate to X-Perps prices by the current price ratio.
    bid, ask, last = okx.ticker(inst["instId"])
    ref = panel.bars[sig.coin].close.iloc[-1]
    ratio = last / ref
    entry = sig.entry.price * ratio if sig.entry.kind == "limit" else (ask if sig.side == "long" else bid)
    stop, target = sig.stop * ratio, sig.target * ratio
    s = 1 if sig.side == "long" else -1
    if sig.entry.kind == "market" and s * (entry - stop) <= 0:
        journal.veto(strat["id"], coin, sig.side, "system", "price already beyond the stop")
        report.append(f"- {strat['id']} {sig.side} {coin}: price already beyond the stop, skipped")
        return
    mode = broker.mode
    open_trades = journal.trades("mode = ? AND status IN ('pending', 'open')", (mode,))
    acc = broker.account()
    one_r = acc["equity"] * limits["risk_per_trade_pct"] / 100
    beta = live_data.beta_to_btc(panel, sig.coin, t_sess)
    by_base = {live_data.base_coin(c): c for c in panel.bars}
    positions = [Position(t["coin"], t["side"], t["risk_usd"] / one_r,
                          live_data.beta_to_btc(panel, by_base[t["coin"]], t_sess) if t["coin"] in by_base else 1.0) for t in open_trades]
    positions += args.__dict__.setdefault("dry_positions", [])  # a dry run counts its cards like real orders
    peak = journal.get(f"peak_{mode}", acc["equity"])
    account = Account(acc["equity"], max(peak, acc["equity"]), acc["available"], positions,
                      pnl_R(journal, mode, broker, one_r, pd.Timestamp.now(tz="UTC").floor("D")),
                      pnl_R(journal, mode, broker, one_r, pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)), journal.get("halted"))
    prop = Proposal(coin, sig.side, entry, stop, target, stage_mult(journal, strat["id"], strat["stage"]), beta,
                    inst["ct_val"], inst["lot_sz"], inst["min_sz"])
    sized, why = check(prop, account, limits)
    if why:
        journal.veto(strat["id"], coin, sig.side, "risk", why)
        report.append(f"- {strat['id']} {sig.side} {coin}: refused by risk engine: {why}")
        return
    text = card(sig, strat, inst, prop, sized, mode, beta)
    print("\n" + text)
    if args.dry_run:
        args.dry_positions.append(Position(coin, sig.side, sized.risk_R, beta))
        report.append(f"- dry run, not placed:\n```\n{text}\n```")
        return
    answer = "y" if (args.approve_all and mode == "paper") else input("Approve? [y/N] ").strip().lower()
    if answer != "y":
        why = input("Reason for rejecting: ").strip() or "no reason given"
        journal.veto(strat["id"], coin, sig.side, "lead", why, text)
        report.append(f"- rejected by the lead: {strat['id']} {sig.side} {coin} ({why})")
        return
    cl = "s" + hashlib.sha1(f"{strat['id']}{coin}{t_sess}".encode()).hexdigest()[:20]
    journal.add_trade(id=cl, mode=mode, strategy=strat["id"], params=json.dumps(strat["params"]), coin=coin, inst_id=inst["instId"],
                      side=sig.side, kind=sig.entry.kind, planned_entry=entry, stop=stop, target=target, contracts=sized.contracts,
                      ct_val=inst["ct_val"], one_r_usd=one_r, risk_usd=sized.risk_usd, leverage=sized.leverage,
                      time_limit_h=sig.time_limit / pd.Timedelta(hours=1), signal_time=t_sess.isoformat(), placed_at=now(), status="pending")
    try:
        broker.place_entry(inst["instId"], sig.side, sized.contracts, sig.entry.kind, entry, stop, target, sized.leverage, cl)
        report.append(f"- placed: {strat['id']} {sig.side} {coin}, {sized.risk_R:.2f}R")
    except okx.OkxError as e:
        journal.update(cl, status="cancelled", closed_at=now(), exit_reason=f"order error: {e}")
        report.append(f"- ORDER FAILED {strat['id']} {sig.side} {coin}: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--approve-all", action="store_true", help="paper mode only: approve every card")
    ap.add_argument("--resume", action="store_true")
    run(ap.parse_args())
