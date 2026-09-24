import json
import subprocess
from pathlib import Path

import pandas as pd

from harness.pit import Panel, PITView
from ingestion.archive import Archive
from llm import funnel, items

RSS = """<?xml version="1.0"?><rss><channel>
<item><guid>a1</guid><title>Binance will list Solana (SOL) perpetual</title><description>&lt;p&gt;New&lt;/p&gt;</description>
<pubDate>Thu, 24 Sep 2026 10:00:00 GMT</pubDate><link>https://x/a1</link></item>
<item><guid>a2</guid><title>Weather is nice</title><pubDate>Thu, 24 Sep 2026 10:00:00 GMT</pubDate></item>
<item><guid>a3</guid><title>Old hack of Ethereum bridge</title><pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def archive(tmp_path):
    a = Archive(tmp_path)
    a.write("text/coindesk", {"url": "u", "status": 200, "date": None, "body": RSS})
    a.write("text/coindesk", {"url": "u", "status": 200, "date": None, "body": RSS})  # same items again: not new
    a.write("text/coinbase_products", {"url": "u", "status": 200, "date": None, "body": json.dumps([{"id": "BTC-USD", "status": "online"}])})
    a.write("text/coinbase_products", {"url": "u", "status": 200, "date": None,
                                       "body": json.dumps([{"id": "BTC-USD", "status": "online"}, {"id": "HYPE-USD", "status": "online"}])})
    a.close()
    return tmp_path / "text"


def test_items_first_seen_and_listing_diff(tmp_path):
    df = items.items(archive(tmp_path))
    assert len(df) == 4 and df.item_id.is_unique
    assert "coinbase_products: new listing HYPE-USD" in set(df.title)
    assert df[df.title.str.startswith("Binance")].text.iloc[0] == "New"


def test_coin_matching_and_freshness(tmp_path):
    known = {"SOL", "ETH", "ONE", "BTC"}
    assert funnel.coins_in("Binance will list Solana (SOL)", known) == ["SOL"]
    assert funnel.coins_in("ONE thing about bitcoin", known) == ["BTC"]  # ambiguous ticker needs $ or ()
    assert funnel.coins_in("rally in $ONE", known) == ["ONE"]
    df = items.items(archive(tmp_path))
    df["first_seen"] = pd.Timestamp("2026-09-24 12:00", tz="UTC")
    cs = funnel.clusters(df, known)
    titles = {c["items"][0]["title"] for c in cs}
    assert "Binance will list Solana (SOL) perpetual" in titles
    assert "Old hack of Ethereum bridge" not in titles and "Weather is nice" not in titles


def test_extract_caches_validates_and_is_point_in_time(tmp_path, monkeypatch):
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        ev = {"event_type": "listing", "coins": ["SOL"], "direction": 0.4, "surprise": 0.5, "credibility": 0.9,
              "time_sensitivity_h": 24, "summary": "Binance lists a SOL perpetual."}
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"is_error": False, "result": "```json\n" + json.dumps(ev) + "\n```"}), "")

    monkeypatch.setattr(funnel, "items", lambda: items.items(archive(tmp_path / "raw")))
    new = funnel.run(out=tmp_path / "llm", runner=fake)
    assert len(new) >= 1 and all(e["valid"] for e in new) and calls[0][:2] == ["claude", "-p"]
    assert "--model" in calls[0] and funnel.config()["model"] in calls[0]
    n = len(calls)
    assert funnel.run(out=tmp_path / "llm", runner=fake) == [] and len(calls) == n  # nothing new, no calls

    events = pd.read_parquet(tmp_path / "llm" / "events.parquet")
    t_known = events.available_time.max()
    bars = pd.DataFrame({"event_time": [t_known - pd.Timedelta(hours=2)], "available_time": [t_known - pd.Timedelta(hours=1)],
                         "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "quote_volume": [1.0]})
    p = Panel({"SOL": bars}, {}, top_n=1, min_days=0, events=events[events.valid])
    assert len(PITView(p, t_known - pd.Timedelta(seconds=1)).events()) == 0  # extracted later: not visible yet
    assert len(PITView(p, t_known).events()) == len(events)


def test_unusable_answer_is_kept_but_marked(tmp_path):
    bad = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps({"is_error": False, "result": "I think it is bullish"}), "")
    c = {"items": [{"source": "x", "first_seen": pd.Timestamp("2026-09-24", tz="UTC"), "title": "t", "text": ""}]}
    ev = funnel.extract(c, funnel.config(), tmp_path, bad)
    assert ev["valid"] is False and "bullish" in ev["raw"]


def test_recall_audit_counts_misses(tmp_path, monkeypatch):
    monkeypatch.setattr(funnel, "items", lambda: items.items(archive(tmp_path)))
    say = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "yes" if "Weather" in cmd[2] else "no"}), "")
    rate, misses = funnel.audit(n=10, runner=say, seed=0)
    assert misses == ["Weather is nice"] and 0 < rate <= 1
