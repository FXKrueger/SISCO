"""OKX X-Perps (EEA): full order book (snapshot + increments), trades, funding, open interest.

Records every live X-Perp, not a liquidity-filtered subset: the universe filter is applied
later, point-in-time. The instrument list itself is archived by the text poller.
"""

import asyncio

from .ws import get_json, stream

REST = "https://eea.okx.com/api/v5/public/instruments?instType=FUTURES"
WS = "wss://wseea.okx.com:8443/ws/v5/public"
CHANNELS = ("books", "trades", "funding-rate", "open-interest")
PER_CONN = 50  # 50 instruments x 4 channels keeps one subscribe message well under OKX's 64 KB limit


def xperp_ids():
    return sorted(i["instId"] for i in get_json(REST)["data"] if i.get("ruleType") == "xperp" and i["state"] == "live")


async def run(archive):
    ids = await asyncio.to_thread(xperp_ids)
    tasks = [
        asyncio.create_task(
            stream(
                archive,
                "okx_xperps",
                WS,
                [{"op": "subscribe", "args": [{"channel": c, "instId": i} for i in ids[k : k + PER_CONN] for c in CHANNELS]}],
                ping="ping",
                stale=60,
            )
        )
        for k in range(0, len(ids), PER_CONN)
    ]
    try:
        # New listing or delisting: return, and the supervisor restarts with the new list.
        while await asyncio.to_thread(xperp_ids) == ids:
            await asyncio.sleep(3600)
    finally:
        for t in tasks:
            t.cancel()
    archive.write("okx_xperps", meta="instrument list changed, resubscribing")
