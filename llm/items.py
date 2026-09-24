"""Raw text archive -> items (SPEC 8.2 step 1: collect, no LLM).

An item is one headline or announcement. first_seen = ingested_at of the first archived body
that contained it: that is its available_time (SPEC 5.1, news: when our collector received it).
Listing changes are items too: the difference between two snapshots of an exchange's product list.

ponytail: rebuilds from the whole text archive on every run (a few MB per day). Switch to
incremental parsing when that gets slow.
"""

import hashlib
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

from ingestion.archive import read_rows

RAW = Path(os.environ.get("SISCO_DATA", "data")) / "raw" / "text"
LISTINGS = {"coinbase_products": lambda b: {p["id"] for p in b if p.get("status") == "online" and not p.get("trading_disabled")},
            "kraken_assetpairs": lambda b: set(b["result"]),
            "okx_eea_instruments": lambda b: {i["instId"] for i in b["data"] if i.get("ruleType") == "xperp" and i["state"] == "live"}}
TAG = re.compile(r"<[^>]+>")


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", s or ""))).strip()


def _xml(body):
    root = ET.fromstring(body.encode())
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for it in root.iter("item"):
        yield {"key": it.findtext("guid") or it.findtext("link") or it.findtext("title"), "title": it.findtext("title"),
               "text": it.findtext("description"), "url": it.findtext("link"), "published": it.findtext("pubDate")}
    for it in root.iterfind("a:entry", ns):
        link = it.find("a:link", ns)
        yield {"key": it.findtext("a:id", namespaces=ns), "title": it.findtext("a:title", namespaces=ns),
               "text": it.findtext("a:summary", namespaces=ns) or it.findtext("a:content", namespaces=ns),
               "url": link.get("href") if link is not None else None, "published": it.findtext("a:updated", namespaces=ns)}


def _json(name, b):
    if name in ("okx_announcements", "okx_eea_announcements"):
        for d in b["data"]:
            for a in d["details"]:
                yield {"key": a["url"], "title": a["title"], "text": a.get("annType"), "url": a["url"], "published": a.get("pTime")}
    elif name == "binance_announcements":
        for c in b["data"]["catalogs"]:
            for a in c["articles"]:
                yield {"key": a["code"], "title": a["title"], "text": c["catalogName"], "url": f"https://www.binance.com/en/support/announcement/{a['code']}",
                       "published": a.get("releaseDate")}
    elif name == "bybit_announcements":
        for a in b["result"]["list"]:
            yield {"key": a["url"], "title": a["title"], "text": a.get("description"), "url": a["url"], "published": a.get("publishTime")}
    elif name == "defillama_hacks":
        for h in b:
            title = f"Hack: {h['name']} lost {h.get('amount') or 0:,.0f} USD ({h.get('technique') or h.get('classification')})"
            yield {"key": f"{h['name']}{h['date']}", "title": title, "text": ", ".join(h.get("chain") or []), "url": h.get("source"), "published": h["date"]}


def items(raw=RAW):
    """All items in the text archive, one row per item, with first_seen."""
    rows, seen = [], {}
    for src in sorted(p for p in raw.iterdir() if p.is_dir()):
        name, prev = src.name, None
        for f in sorted(src.rglob("*.jsonl.zst")):
            for r in read_rows(f):
                if "data" not in r or r["data"].get("status") != 200:
                    continue
                body = r["data"]["body"]
                t = pd.Timestamp(r["ingested_at"], unit="ns", tz="UTC")
                try:
                    if name in LISTINGS:
                        now = LISTINGS[name](json.loads(body))
                        if prev is not None:
                            for sym in sorted(now - prev):
                                rows.append(_row(name, f"listed {sym}", f"{name}: new listing {sym}", "", None, None, t, seen))
                            for sym in sorted(prev - now):
                                rows.append(_row(name, f"removed {sym}", f"{name}: delisted or suspended {sym}", "", None, None, t, seen))
                        prev = now
                        continue
                    gen = _xml(body) if body.lstrip().startswith("<") else _json(name, json.loads(body))
                    for it in gen:
                        rows.append(_row(name, it["key"], it["title"], it["text"], it["url"], it["published"], t, seen))
                except (ET.ParseError, ValueError, KeyError, TypeError):
                    continue  # a malformed body: skip it, the raw archive keeps it
    df = pd.DataFrame([r for r in rows if r])
    return df.sort_values("first_seen").reset_index(drop=True) if len(df) else df


def _row(source, key, title, text, url, published, t, seen):
    item_id = hashlib.sha256(f"{source}|{key}".encode()).hexdigest()[:16]
    if item_id in seen:
        return None
    seen[item_id] = t
    return {"item_id": item_id, "source": source, "title": clean(title), "text": clean(text)[:1000], "url": url,
            "published": str(published) if published is not None else None, "first_seen": t}
