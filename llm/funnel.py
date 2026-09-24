"""The LLM funnel (SPEC 8.2): local filter and clustering, then Opus extraction per story cluster.

  python -m llm.funnel             extract new clusters within today's call budget
  python -m llm.funnel --dry-run   show what would be extracted, no LLM calls
  python -m llm.funnel --audit     weekly recall audit: the model checks 20 discarded items (SPEC 8.2 step 5)

Every extraction is cached by a hash of prompt, model and CLI version, so a rerun never calls the
model twice for the same input. Events get available_time = when the extraction finished
(SPEC 5.1). The model and CLI version are pinned in config/llm.yaml; a different installed CLI
version is refused (SPEC 8.4: a new model or CLI is a new signal).
"""

import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

from .items import items

ROOT = Path(__file__).parents[1]
OUT = Path(os.environ.get("SISCO_DATA", "data")) / "llm"
CONFIG = ROOT / "config" / "llm.yaml"
EVENT_TYPES = ["listing", "delisting", "unlock", "hack", "regulatory", "macro", "etf_flow", "governance", "partnership", "other"]
MACRO = re.compile(r"\b(?:SEC|CFTC|ESMA|BaFin|MiCA|ETF|Fed|FOMC|CPI|rate (?:cut|hike)|tariff|stablecoin|hack|exploit|drain|listing|delist|"
                   r"will list|will delist|unlock|lawsuit|charges|approv|ban)\w*", re.I)
NOISE = re.compile(r"daily (?:general )?discussion|giveaway|token splash|prize pool|trading competition|airdrop campaign", re.I)
NAMES = {"bitcoin": "BTC", "ethereum": "ETH", "ether": "ETH", "solana": "SOL", "ripple": "XRP", "dogecoin": "DOGE", "cardano": "ADA",
         "chainlink": "LINK", "avalanche": "AVAX", "polkadot": "DOT", "litecoin": "LTC", "tron": "TRX", "toncoin": "TON",
         "hyperliquid": "HYPE", "uniswap": "UNI", "aave": "AAVE", "sui": "SUI", "near protocol": "NEAR", "binance coin": "BNB"}
# Tickers that are also common words: only matched with a $ prefix or inside parentheses.
AMBIGUOUS = {"ONE", "GAS", "ME", "ID", "AI", "OP", "BAT", "ANY", "SUN", "TRUMP", "PEOPLE", "HIGH", "LOW", "GOOD", "BOND", "FUN", "OM", "IO", "T", "S"}


def config():
    return yaml.safe_load(CONFIG.read_text())


def tickers():
    syms = (ROOT / "config" / "research_symbols.txt").read_text().split()
    return {s.removesuffix("USDT").lstrip("0123456789") for s in syms}


def coins_in(text, known):
    found = {NAMES[n] for n in NAMES if re.search(rf"\b{n}\b", text, re.I)}
    for m in re.finditer(r"(\$|\()?\b([A-Z0-9]{2,10})\b(\))?", text):
        sym = m.group(2)
        if sym in known and (sym not in AMBIGUOUS or m.group(1)):
            found.add(sym)
    return sorted(found)


def fresh(row, max_age_days=3):
    """Only news that was new when we first saw it. The first fetch of a feed returns its whole
    history (DefiLlama: every hack since 2022); those items are old, not news."""
    p = row["published"]
    if p is None:
        return True
    try:
        ts = pd.Timestamp(int(p), unit="s" if int(p) < 1e11 else "ms", tz="UTC") if str(p).isdigit() else pd.Timestamp(p)
        ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
    except (ValueError, TypeError, OverflowError):
        return True
    return row["first_seen"] - ts <= pd.Timedelta(days=max_age_days)


def clusters(df, known):
    """Relevant fresh items grouped into stories: same coins, similar title, within 24 hours."""
    df = df[df.apply(fresh, axis=1)].copy()
    df["coins"] = [coins_in(f"{t} {x}", known) for t, x in zip(df.title, df.text)]
    df = df[((df.coins.str.len() > 0) | df.title.str.contains(MACRO)) & ~df.title.str.contains(NOISE)]
    out = []
    for _, r in df.sort_values("first_seen").iterrows():
        for c in out:
            if (r.first_seen - c["first_seen"] <= pd.Timedelta(hours=24) and set(r.coins) == set(c["coins"])
                    and difflib.SequenceMatcher(None, r.title.lower(), c["items"][0]["title"].lower()).ratio() > 0.6):
                c["items"].append(r.to_dict())
                break
        else:
            out.append({"first_seen": r.first_seen, "coins": r.coins, "items": [r.to_dict()]})
    for c in out:
        c["cluster_id"] = hashlib.sha256("|".join(sorted(i["item_id"] for i in c["items"][:1])).encode()).hexdigest()[:16]
    return out


PROMPT = """You extract one structured market event from a cluster of crypto news items.
Answer with one JSON object only, no prose, with exactly these keys:
event_type: one of {types}
coins: list of affected coin tickers (uppercase), empty if market-wide
direction: number from -1 (clearly bearish for those coins) to +1 (clearly bullish), 0 if neutral or unclear
surprise: number from 0 (fully expected, already known) to 1 (complete surprise)
credibility: number from 0 (rumor, single unverified source) to 1 (official announcement)
time_sensitivity_h: hours over which this is likely to move prices
summary: one factual sentence, no speculation
Judge only from the items below. Do not use knowledge of what happened later.

Items (source, first seen UTC, title, text):
{items}"""


def extract(cluster, cfg, cache_dir, run=subprocess.run):
    """One Opus call per cluster, cached. Returns the event dict or None if the answer is unusable."""
    body = "\n".join(f"- {i['source']}, {pd.Timestamp(i['first_seen']):%Y-%m-%d %H:%M}, {i['title']} | {i['text'][:300]}" for i in cluster["items"][:8])
    prompt = PROMPT.format(types=", ".join(EVENT_TYPES), items=body)
    key = hashlib.sha256(f"{cfg['prompt_version']}|{cfg['model']}|{cfg['cli_version']}|{prompt}".encode()).hexdigest()
    path = cache_dir / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    res = run(["claude", "-p", prompt, "--model", cfg["model"], "--output-format", "json"], capture_output=True, text=True, timeout=600)
    try:
        out = json.loads(res.stdout)
    except json.JSONDecodeError:
        out = {"is_error": True, "result": res.stderr[:300] or res.stdout[:300]}
    if res.returncode != 0 or out.get("is_error"):
        raise RuntimeError(f"claude -p failed: {out.get('result', '')[:300]}")
    text = out.get("result", "")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        ev = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        ev = None
    ok = (isinstance(ev, dict) and ev.get("event_type") in EVENT_TYPES and isinstance(ev.get("coins"), list)
          and all(isinstance(ev.get(k), (int, float)) for k in ("direction", "surprise", "credibility", "time_sensitivity_h"))
          and -1 <= ev["direction"] <= 1 and 0 <= ev["surprise"] <= 1 and 0 <= ev["credibility"] <= 1)
    ev = {**ev, "valid": True} if ok else {"valid": False, "raw": text[:2000]}
    ev.update(available_time=pd.Timestamp.now(tz="UTC").isoformat(), model_id=cfg["model"], cli_version=cfg["cli_version"],
              prompt_version=cfg["prompt_version"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ev))
    return ev


def check_cli(cfg):
    v = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.split()[:1]
    if v != [cfg["cli_version"]]:
        sys.exit(f"installed Claude Code {v} != pinned {cfg['cli_version']} in config/llm.yaml. A different CLI or model is a "
                 "new signal (SPEC 8.4): update the pin by PR and run the new version in shadow mode first.")


def run(dry_run=False, out=OUT, runner=subprocess.run, max_calls=None):
    cfg = config()
    if not dry_run and runner is subprocess.run:
        check_cli(cfg)
    events_path = out / "events.parquet"
    done = set(pd.read_parquet(events_path).story_cluster_id) if events_path.exists() else set()
    today = pd.Timestamp.now(tz="UTC").floor("D")
    spent = sum(1 for e in (pd.read_parquet(events_path).available_time if events_path.exists() else []) if pd.Timestamp(e) >= today)
    todo = [c for c in clusters(items(), tickers()) if c["cluster_id"] not in done]
    # Priority: coin-specific stories, then more sources, then newest (SPEC 8.3 priority queue).
    todo.sort(key=lambda c: (not c["coins"], -len(c["items"]), -c["first_seen"].value))
    budget = max(0, cfg["daily_call_budget"] - spent)
    if max_calls is not None:
        budget = min(budget, max_calls)
    print(f"{len(todo)} new story clusters, budget left today {budget}")
    new = []
    for c in todo[:budget]:
        if dry_run:
            print(f"  would extract: {c['coins']} {c['items'][0]['title'][:90]}")
            continue
        ev = extract(c, cfg, out / "cache", runner)
        new.append({"event_id": hashlib.sha256((c["cluster_id"] + ev["available_time"]).encode()).hexdigest()[:16],
                    "story_cluster_id": c["cluster_id"], "event_type": ev.get("event_type"), "coins": json.dumps(ev.get("coins", [])),
                    "direction": ev.get("direction"), "surprise": ev.get("surprise"), "credibility": ev.get("credibility"),
                    "time_sensitivity_h": ev.get("time_sensitivity_h"), "summary": ev.get("summary"), "valid": ev["valid"],
                    "source_ids": json.dumps([i["item_id"] for i in c["items"]]), "first_seen": c["first_seen"],
                    "model_id": ev["model_id"], "cli_version": ev["cli_version"], "prompt_version": ev["prompt_version"],
                    "available_time": pd.Timestamp(ev["available_time"])})
    if new:
        df = pd.DataFrame(new)
        if events_path.exists():
            df = pd.concat([pd.read_parquet(events_path), df])
        out.mkdir(parents=True, exist_ok=True)
        df.to_parquet(events_path, index=False)
        print(f"{len(new)} events extracted, {sum(not e['valid'] for e in new)} unusable answers")
    return new


AUDIT = """Would a crypto trader want to know this headline within a few days because it could move the
price of a specific coin or the crypto market? Answer only yes or no.

Headline: {title}
Text: {text}"""


def audit(n=20, runner=subprocess.run, seed=None):
    """Recall audit: sample fresh items the local filter discarded and ask the model whether any
    were relevant. The miss rate tells whether the filter throws away real news."""
    cfg = config()
    if runner is subprocess.run:
        check_cli(cfg)
    df = items()
    df = df[df.apply(fresh, axis=1)]
    kept = {i["item_id"] for c in clusters(df, tickers()) for i in c["items"]}
    dropped = df[~df.item_id.isin(kept)]
    sample = dropped.sample(min(n, len(dropped)), random_state=seed)
    misses = []
    for _, r in sample.iterrows():
        res = runner(["claude", "-p", AUDIT.format(title=r.title, text=r.text[:300]), "--model", cfg["model"], "--output-format", "json"],
                     capture_output=True, text=True, timeout=600)
        if json.loads(res.stdout).get("result", "").strip().lower().startswith("yes"):
            misses.append(r.title)
    rate = len(misses) / max(len(sample), 1)
    print(f"recall audit: {len(misses)} of {len(sample)} discarded items were relevant (miss rate {rate:.0%})")
    for m in misses:
        print(f"  missed: {m}")
    return rate, misses


if __name__ == "__main__":
    if "--audit" in sys.argv:
        audit()
    else:
        run(dry_run="--dry-run" in sys.argv)
