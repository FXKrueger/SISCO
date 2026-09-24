import base64
import hashlib
import hmac
import io
import json

import pytest

from execution import okx


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setattr(okx, "xperp_instruments", lambda: {"BTC": {"instId": "BTC-X", "ct_val": 0.0001, "lot_sz": 1, "min_sz": 1, "tick_sz": 0.1, "max_lever": 50}})
    sent = []

    def fake(req, timeout):
        sent.append(req)
        return Resp(json.dumps({"code": "0", "data": [{"ordId": "1"}]}).encode())

    monkeypatch.setattr(okx.urllib.request, "urlopen", fake)
    b = okx.OkxBroker(demo=True, key="k", secret="s", passphrase="p")
    b.sent = sent
    return b


def test_signed_entry_with_attached_stop_and_target(broker):
    broker.place_entry("BTC-X", "long", 1000, "limit", 99_999.94, 95_000.06, 115_000.0, 5, "sabc")
    lev, order = broker.sent
    h = {k.lower(): v for k, v in order.header_items()}
    body = order.data.decode()
    expect = base64.b64encode(hmac.new(b"s", (h["ok-access-timestamp"] + "POST" + "/api/v5/trade/order" + body).encode(), hashlib.sha256).digest()).decode()
    assert h["ok-access-sign"] == expect and h["x-simulated-trading"] == "1" and h["ok-access-passphrase"] == "p"
    b = json.loads(body)
    assert (b["tdMode"], b["side"], b["ordType"], b["sz"], b["px"]) == ("isolated", "buy", "limit", "1000", "99999.9")
    a = b["attachAlgoOrds"][0]
    assert (a["slTriggerPx"], a["slOrdPx"], a["tpTriggerPx"], a["tpOrdPx"]) == ("95000.1", "-1", "115000", "-1")
    assert json.loads(lev.data)["mgnMode"] == "isolated" and json.loads(lev.data)["lever"] == "5"


def test_error_counting(broker, monkeypatch):
    monkeypatch.setattr(okx.urllib.request, "urlopen", lambda req, timeout: Resp(b'{"code": "51000", "msg": "bad", "data": []}'))
    for n in (1, 2):
        with pytest.raises(okx.OkxError):
            broker.pending()
        assert broker.errors == n
