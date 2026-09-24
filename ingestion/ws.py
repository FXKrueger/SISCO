import asyncio
import json
import logging
import urllib.request

import websockets

log = logging.getLogger("ingestion")
UA = "SISCO-archiver/0.1"
REDDIT_UA = "python:sisco-archiver:v0.1 (research archive)"  # Reddit 429s other formats


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": REDDIT_UA if "reddit.com" in url else UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers, r.read()


def get_json(url):
    return json.loads(http_get(url)[2])


async def stream(archive, source, url, subscribe=(), ping=None, stale=None, on_msg=None):
    """Archive every message from a websocket, forever.

    url: str, or a callable returning the url (evaluated on each connect).
    ping: text frame to send after 25 s of silence (OKX needs "ping"). The reply is not archived.
    stale: reconnect if no data for this many seconds. For feeds that are never quiet.
    on_msg: called with each archived message.
    """
    delay = 1
    while True:
        try:
            async with websockets.connect(url() if callable(url) else url, max_size=None, open_timeout=15) as ws:
                for msg in subscribe:
                    await ws.send(json.dumps(msg))
                archive.write(source, meta="connected")
                silent = 0.0
                while True:
                    wait = 25 if ping else stale
                    try:
                        msg = await asyncio.wait_for(ws.recv(), wait)
                    except TimeoutError:
                        silent += wait
                        if stale and silent >= stale:
                            raise TimeoutError(f"no data for {silent:.0f}s")
                        await ws.send(ping)
                        continue
                    if isinstance(msg, bytes):
                        msg = msg.decode()
                    if msg == "pong":
                        archive.seen(source)
                        continue
                    silent = 0.0
                    delay = 1
                    if '"event":"error"' in msg[:40]:
                        log.error("%s: %s", source, msg[:300])
                    archive.write(source, msg)
                    if on_msg:
                        on_msg(msg)
        except Exception as e:
            archive.write(source, meta=f"disconnected: {e!r}")
            log.warning("%s disconnected: %r, retry in %ss", source, e, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)
