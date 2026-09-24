"""OKX X-Perps adapter (EEA, API v5). Protected: changes need the lead's approval.

Live and demo trading use the same code; demo adds the x-simulated-trading header and needs
demo API keys. Keys come from the environment (SISCO_OKX_KEY, SISCO_OKX_SECRET,
SISCO_OKX_PASSPHRASE), set by the lead in ~/.sisco/okx.env. They never appear in the repo.

Every entry goes out with its stop and target attached (attachAlgoOrds), so both are placed by
the exchange together with the entry (SPEC 11, D19). Both are market orders on trigger: with
OKX the stop and target form one OCO pair, and a target limit order that did not fill after its
trigger would leave the position without a stop. Isolated margin, net position mode.
"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

BASE = "https://eea.okx.com"


class OkxError(Exception):
    pass


def _get_public(path, params=None):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "sisco"}), timeout=20) as r:
        body = json.loads(r.read())
    if body.get("code") != "0":
        raise OkxError(f"{path}: {body.get('code')} {body.get('msg')}")
    return body["data"]


def xperp_instruments():
    """coin -> instrument spec for every live X-Perp."""
    out = {}
    for i in _get_public("/api/v5/public/instruments", {"instType": "FUTURES"}):
        if i.get("ruleType") == "xperp" and i["state"] == "live":
            out[i["instFamily"].split("-")[0]] = {
                "instId": i["instId"], "ct_val": float(i["ctVal"]) * float(i.get("ctMult") or 1),
                "lot_sz": float(i["lotSz"]), "min_sz": float(i["minSz"]), "tick_sz": float(i["tickSz"]),
                "max_lever": float(i["lever"]),
            }
    return out


def funding_history(inst_id, since_ms):
    """Settled funding [(ms, rate)] since since_ms, oldest first."""
    rows = _get_public("/api/v5/public/funding-rate-history", {"instId": inst_id, "limit": 100})
    return sorted((int(r["fundingTime"]), float(r["realizedRate"] or r["fundingRate"])) for r in rows if int(r["fundingTime"]) > since_ms)


def ticker(inst_id):
    t = _get_public("/api/v5/market/ticker", {"instId": inst_id})[0]
    return float(t["bidPx"]), float(t["askPx"]), float(t["last"])


def candles(inst_id, after_ms=None, limit=100):
    """1h candles, newest first: [ts_ms, open, high, low, close, confirmed]."""
    params = {"instId": inst_id, "bar": "1H", "limit": limit}
    if after_ms:
        params["after"] = after_ms
    rows = _get_public("/api/v5/market/history-candles", params)
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), r[8] == "1") for r in rows]


def server_time_ms():
    return int(_get_public("/api/v5/public/time")[0]["ts"])


def round_px(px, tick):
    """Price as an exact multiple of the tick size, as a plain decimal string."""
    t = Decimal(str(tick))
    return format(((Decimal(str(px)) / t).quantize(Decimal(1), ROUND_HALF_UP) * t).normalize(), "f")


class OkxBroker:
    def __init__(self, demo, key=None, secret=None, passphrase=None):
        self.demo = demo
        self.key = key or os.environ["SISCO_OKX_KEY"]
        self.secret = secret or os.environ["SISCO_OKX_SECRET"]
        self.passphrase = passphrase or os.environ["SISCO_OKX_PASSPHRASE"]
        self.errors = 0  # consecutive API errors, for the kill switch
        self.instruments = xperp_instruments()

    @property
    def mode(self):
        return "demo" if self.demo else "live"

    def _req(self, method, path, params=None, body=None):
        query = "?" + urllib.parse.urlencode(params) if params else ""
        data = json.dumps(body) if body is not None else ""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        sign = base64.b64encode(hmac.new(self.secret.encode(), (ts + method + path + query + data).encode(), hashlib.sha256).digest()).decode()
        headers = {"OK-ACCESS-KEY": self.key, "OK-ACCESS-SIGN": sign, "OK-ACCESS-TIMESTAMP": ts,
                   "OK-ACCESS-PASSPHRASE": self.passphrase, "Content-Type": "application/json", "User-Agent": "sisco"}
        if self.demo:
            headers["x-simulated-trading"] = "1"
        req = urllib.request.Request(BASE + path + query, data=data.encode() if data else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                out = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError) as e:
            self.errors += 1
            raise OkxError(f"{method} {path}: {e!r}") from e
        if out.get("code") != "0":
            self.errors += 1
            detail = [(d.get("sCode"), d.get("sMsg")) for d in out.get("data", []) if isinstance(d, dict)]
            raise OkxError(f"{method} {path}: {out.get('code')} {out.get('msg')} {detail}")
        self.errors = 0
        return out["data"]

    # --- account setup (idempotent, run by the session before trading) ---
    def setup(self):
        cfg = self._req("GET", "/api/v5/account/config")[0]
        if cfg.get("posMode") != "net_mode":
            self._req("POST", "/api/v5/account/set-position-mode", body={"posMode": "net_mode"})

    # --- state ---
    def account(self):
        d = self._req("GET", "/api/v5/account/balance")[0]
        avail = sum(float(c.get("availEq") or c.get("availBal") or 0) for c in d.get("details", []))
        return {"equity": float(d["totalEq"]), "available": avail}

    def positions(self):
        out = []
        for p in self._req("GET", "/api/v5/account/positions", {"instType": "FUTURES"}):
            qty = float(p["pos"] or 0)
            if qty:
                out.append({"instId": p["instId"], "side": "long" if qty > 0 else "short", "contracts": abs(qty),
                            "avg_px": float(p["avgPx"]), "upl": float(p["upl"] or 0)})
        return out

    def pending(self):
        return [{"instId": o["instId"], "clOrdId": o["clOrdId"], "ordId": o["ordId"]}
                for o in self._req("GET", "/api/v5/trade/orders-pending", {"instType": "FUTURES"})]

    def closed_positions(self, since_ms):
        """Realized PnL per closed position, fees and funding included (USD)."""
        rows = self._req("GET", "/api/v5/account/positions-history", {"instType": "FUTURES", "after": str(int(time.time() * 1000))})
        # OKX: pnl is the price PnL, fee is negative when charged, fundingFee is signed (received > 0).
        return [{"instId": r["instId"], "closed_ms": int(r["uTime"]), "pnl": float(r.get("pnl") or 0),
                 "fee": -float(r.get("fee") or 0), "funding": float(r.get("fundingFee") or 0), "close_px": float(r["closeAvgPx"])}
                for r in rows if int(r["uTime"]) >= since_ms]

    # --- orders ---
    def place_entry(self, inst_id, side, contracts, kind, px, stop, target, leverage, cl_ord_id):
        spec = next(v for v in self.instruments.values() if v["instId"] == inst_id)
        self._req("POST", "/api/v5/account/set-leverage", body={"instId": inst_id, "lever": str(leverage), "mgnMode": "isolated"})
        body = {"instId": inst_id, "tdMode": "isolated", "side": "buy" if side == "long" else "sell", "clOrdId": cl_ord_id,
                "ordType": "limit" if kind == "limit" else "market", "sz": str(int(contracts)) if contracts == int(contracts) else str(contracts),
                "attachAlgoOrds": [{"attachAlgoClOrdId": cl_ord_id + "a", "slTriggerPx": round_px(stop, spec["tick_sz"]), "slOrdPx": "-1",
                                    "tpTriggerPx": round_px(target, spec["tick_sz"]), "tpOrdPx": "-1",
                                    "slTriggerPxType": "last", "tpTriggerPxType": "last"}]}
        if kind == "limit":
            body["px"] = round_px(px, spec["tick_sz"])
        return self._req("POST", "/api/v5/trade/order", body=body)[0]["ordId"]

    def cancel(self, inst_id, cl_ord_id):
        self._req("POST", "/api/v5/trade/cancel-order", body={"instId": inst_id, "clOrdId": cl_ord_id})

    def close(self, inst_id):
        """Market-close a position and cancel its attached stop and target."""
        self._req("POST", "/api/v5/trade/close-position", body={"instId": inst_id, "mgnMode": "isolated", "autoCxl": True})
        for kind in ("oco", "conditional"):
            algos = [a for a in self._req("GET", "/api/v5/trade/orders-algo-pending", {"instType": "FUTURES", "ordType": kind})
                     if a["instId"] == inst_id]
            if algos:
                self._req("POST", "/api/v5/trade/cancel-algos", body=[{"algoId": a["algoId"], "instId": inst_id} for a in algos])

    def protected(self, inst_id):
        """True if a stop (algo order) is live for this instrument."""
        for kind in ("oco", "conditional"):
            if any(a["instId"] == inst_id for a in self._req("GET", "/api/v5/trade/orders-algo-pending", {"instType": "FUTURES", "ordType": kind})):
                return True
        return False
