"""Raw text archive: poll feeds and endpoints, store the full body whenever it changes.

No parsing here. Parsing, dedup and entity matching happen downstream (SPEC 8.2), so a parser
bug can never lose raw data. available_time of an item = ingested_at of the first body that
contains it (when our collector received it, SPEC 5.1).

Only sources whose past cannot be fetched later with correct receive times belong here.
Not here, because history is downloadable later: GDELT, Snapshot/Tally, token unlock schedules.
Not here yet: Telegram (needs API credentials), X (deferred to phase C, D16), BaFin (no working feed found).
"""

import asyncio
import hashlib
import logging
import random

from .ws import http_get

log = logging.getLogger("ingestion")

# name -> (url, poll seconds)
SOURCES = {
    # Exchange announcements and listings (time-critical)
    "okx_announcements": ("https://www.okx.com/api/v5/support/announcements", 60),
    "okx_eea_announcements": ("https://eea.okx.com/api/v5/support/announcements", 60),
    "okx_eea_instruments": ("https://eea.okx.com/api/v5/public/instruments?instType=FUTURES", 300),
    "binance_announcements": ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20", 60),
    "bybit_announcements": ("https://api.bybit.com/v5/announcements/index?locale=en-US&limit=20", 60),
    "coinbase_products": ("https://api.exchange.coinbase.com/products", 300),
    "kraken_assetpairs": ("https://api.kraken.com/0/public/AssetPairs", 300),
    # Crypto news
    "coindesk": ("https://www.coindesk.com/arc/outboundfeeds/rss", 120),
    "cointelegraph": ("https://cointelegraph.com/rss", 120),
    "theblock": ("https://www.theblock.co/rss.xml", 120),
    "decrypt": ("https://decrypt.co/feed", 120),
    "bitcoinmagazine": ("https://bitcoinmagazine.com/feed", 300),
    "thedefiant": ("https://thedefiant.io/api/feed", 300),
    "dlnews": ("https://www.dlnews.com/arc/outboundfeeds/rss/", 120),
    "blockworks": ("https://blockworks.com/feed", 120),
    # Hacks
    "rekt": ("https://rekt.news/rss/feed.xml", 600),
    "defillama_hacks": ("https://api.llama.fi/hacks", 900),
    # Regulators and macro
    "sec_press": ("https://www.sec.gov/news/pressreleases.rss", 120),
    "cftc_press": ("https://www.cftc.gov/RSS/RSSGP/rssgp.xml", 300),
    "esma": ("https://www.esma.europa.eu/rss.xml", 300),
    "fed_press": ("https://www.federalreserve.gov/feeds/press_all.xml", 120),
    # Reddit (no reliable way to fetch the past with receive times)
    "reddit": ("https://www.reddit.com/r/CryptoCurrency+Bitcoin+ethereum+CryptoMarkets/new/.rss?limit=100", 120),
}


async def poll(archive, name, url, every):
    source = f"text/{name}"
    last = None
    await asyncio.sleep(random.uniform(0, min(every, 30)))  # spread start-up load (Reddit answers bursts with 429)
    while True:
        try:
            status, headers, body = await asyncio.to_thread(http_get, url)
            archive.seen(source)
            digest = hashlib.sha256(body).hexdigest()
            if digest != last:
                last = digest
                # ponytail: undecodable bytes become U+FFFD. Store base64 if a source ever needs exact bytes.
                archive.write(source, {"url": url, "status": status, "date": headers.get("Date"), "body": body.decode("utf-8", "replace")})
        except Exception as e:
            archive.write(source, meta=f"poll failed: {e!r}")
            log.warning("%s poll failed: %r", name, e)
        await asyncio.sleep(every)


async def run(archive):
    await asyncio.gather(*(poll(archive, name, url, every) for name, (url, every) in SOURCES.items()))
