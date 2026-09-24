"""Liquidation streams. Live only, no history exists anywhere else.

Binance forceOrder sends at most the latest liquidation per symbol per second, so it is a
lower bound on liquidation volume, not a complete record.
Hyperliquid is not here: it has no public liquidation stream, and its fill history
(with liquidation flags) can be downloaded later from its public S3 archive.
"""

import asyncio

from .okx_xperps import WS as OKX_EEA_WS
from .ws import stream


def okx_liq(*inst_types):
    return [{"op": "subscribe", "args": [{"channel": "liquidation-orders", "instType": t} for t in inst_types]}]


async def run(archive):
    await asyncio.gather(
        # Binance moved futures streams to /market in 2026. The old URL connects but stays silent.
        stream(archive, "binance_liquidations", "wss://fstream.binance.com/market/ws/!forceOrder@arr"),
        stream(archive, "okx_liquidations", "wss://ws.okx.com:8443/ws/v5/public", okx_liq("SWAP", "FUTURES"), ping="ping"),
        stream(archive, "okx_xperps_liquidations", OKX_EEA_WS, okx_liq("FUTURES"), ping="ping"),
    )
