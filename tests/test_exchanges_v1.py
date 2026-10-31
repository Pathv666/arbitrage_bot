"""Contratos públicos explícitos; tests no consultan la red."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from p2p_arb_bot.infrastructure.exchanges import (
    BinanceSource, OKXSource, BybitSource, BingXSource, ResilientSource,
)

NOW = datetime.now(timezone.utc)


@pytest.mark.parametrize("side,maker", [("BUY", "SELL"), ("SELL", "BUY")])
def test_binance_side_mapping(side, maker):
    source = BinanceSource()
    method, request = source.request("USDT", "VND", side, 20)
    assert request["json"]["tradeType"] == side and method == "POST"
    a = {"adv": {"advNo": "a1", "price": "25785", "surplusAmount": "1000",
                 "minSingleTransAmount": "100000", "maxSingleTransAmount": "26000000",
                 "tradeType": maker, "payTimeLimit": 15, "tradeMethods": [{"identifier": "VCB"}]},
         "advertiser": {"userNo": "u1", "nickName": "merchant", "monthOrderCount": 100,
                        "monthFinishRate": "0.99", "userType": "merchant"}}
    ad = source.parse(a, side, "USDT", "VND", NOW)
    assert ad.trade_type == side and ad.completion_rate == 99
    assert ad.completed_orders == 100 and ad.verified
    assert ad.asset == "USDT" and ad.fiat == "VND" and ad.timestamp == NOW
    a["adv"]["tradeType"] = side
    with pytest.raises(ValueError): source.parse(a, side, "USDT", "VND", NOW)


@pytest.mark.parametrize("side,maker", [("BUY", "sell"), ("SELL", "buy")])
def test_okx_side_mapping(side, maker):
    source = OKXSource()
    assert source.request("USDT", "VND", side, 20)[1]["params"]["side"] == maker
    raw = {"id": "a1", "price": "26000", "availableAmount": "1000",
           "quoteMinAmountPerOrder": "100000", "quoteMaxAmountPerOrder": "26000000",
           "merchantId": "u1", "nickName": "merchant", "paymentMethods": ["Vietcombank"],
           "side": maker, "completedRate": "0.98", "completedOrderQuantity": "200", "paymentTimeoutMinutes": "15"}
    records = source.records({"code": 0, "data": {maker: [raw]}}, side)
    ad = source.parse(records[0], side, "USDT", "VND", NOW)
    assert ad.trade_type == side and ad.exchange == "OKX" and ad.completion_rate == 98


@pytest.mark.parametrize("side,maker", [("BUY", "1"), ("SELL", "0")])
def test_bybit_side_mapping(side, maker):
    source = BybitSource()
    assert source.request("USDT", "VND", side, 20)[1]["json"]["side"] == maker
    raw = {"id": "a1", "price": "26000", "lastQuantity": "1000", "minAmount": "100000",
           "maxAmount": "26000000", "userId": "u1", "nickName": "merchant", "side": maker,
           "payments": ["493"], "recentExecuteRate": "99", "recentOrderNum": 300, "paymentPeriod": 15}
    ad = source.parse(raw, side, "USDT", "VND", NOW)
    assert ad.exchange == "Bybit" and ad.trade_type == side and ad.completion_rate == 99
    assert ad.pay_methods == ("493",)  # no inventar un nombre de banco
    raw["side"] = "0" if maker == "1" else "1"
    with pytest.raises(ValueError): source.parse(raw, side, "USDT", "VND", NOW)


def test_bingx_experimental_failure_and_internal_contract():
    source = BingXSource()
    with pytest.raises(RuntimeError, match="no verificado"):
        asyncio.run(source.fetch_ads())
    ad = source.parse_contract({"id": "x", "price": "26000", "quantity": "100",
                                "min": "100000", "max": "1000000", "payments": ["VCB"],
                                "merchant": "test"}, "BUY", "USDT", "VND", NOW)
    assert ad.datasource_type == "EXPERIMENTAL_API" and source.timeout_s == 3


def test_resilient_source_retry_circuit_and_recovery():
    class Source:
        exchange = "fake"
        timeout_s = 1
        calls = 0
        broken = True
        async def fetch_ads(self, **kwargs):
            self.calls += 1
            if self.broken: raise ValueError("schema")
            return []
    async def check():
        raw = Source()
        source = ResilientSource(raw, interval_s=0, attempts=1, cooldown_s=100)
        for _ in range(2):
            with pytest.raises(ValueError): await source.fetch_ads()
        with pytest.raises(RuntimeError, match="circuito"): await source.fetch_ads()
        assert raw.calls == 2
        source.open_until = 0
        raw.broken = False
        assert await source.fetch_ads() == [] and source.failures == 0
    asyncio.run(check())


def test_defensive_public_response_does_not_treat_bad_schema_as_liquidity(monkeypatch):
    source = BinanceSource()
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, json=lambda: {"success": True, "data": [{"adv": {"price": "NaN"}}]})
    monkeypatch.setattr(source.client, "_new_session", Session)
    with pytest.raises(ValueError, match="ningún anuncio válido"):
        asyncio.run(source.fetch_ads(asset="USDT", fiat="VND", trade_type="BUY", pay_types=[]))


def test_backoff_and_per_exchange_request_spacing(monkeypatch):
    waits = []
    class Source:
        exchange = "fake"
        timeout_s = 1
        calls = 0
        async def fetch_ads(self, **kwargs):
            self.calls += 1
            if self.calls == 1: raise TimeoutError("timeout")
            return []
    async def fake_sleep(delay): waits.append(delay)
    monkeypatch.setattr("p2p_arb_bot.infrastructure.exchanges.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("p2p_arb_bot.infrastructure.exchanges.random.random", lambda: 0.5)
    async def check():
        source = ResilientSource(Source(), interval_s=2, attempts=2)
        assert await source.fetch_ads() == []
        assert source.source.calls == 2
        assert 1.5 in waits  # backoff exponencial + jitter
        assert max(waits) > 1.9  # segundo intento también limitado por exchange
    asyncio.run(check())


def test_timeout_reaches_circuit_without_hanging():
    class Source:
        exchange = "fake"
        timeout_s = -0.99  # wait_for de 10ms en el wrapper
        async def fetch_ads(self, **kwargs):
            await asyncio.Event().wait()
    async def check():
        source = ResilientSource(Source(), interval_s=0, attempts=1)
        with pytest.raises(TimeoutError): await source.fetch_ads()
    asyncio.run(check())


def test_legacy_binance_keeps_requested_pair_metadata(monkeypatch):
    from p2p_arb_bot.infrastructure.binance_p2p import BinanceP2PSource
    source = BinanceP2PSource()
    async def data(payload):
        return [{"adv": {"advNo": "a", "price": "100", "surplusAmount": "10",
                         "minSingleTransAmount": "1", "maxSingleTransAmount": "1000"},
                 "advertiser": {"userNo": "u"}}]
    monkeypatch.setattr(source, "_post_with_retry", data)
    result = asyncio.run(source.fetch_ads(asset="BTC", fiat="VES", trade_type="BUY", pay_types=[]))
    assert result[0].asset == "BTC" and result[0].fiat == "VES"


def test_bybit_zero_user_id_falls_back_to_nickname():
    from datetime import datetime, timezone
    src = BybitSource()
    item = {
        "id": "ad1", "price": "26000", "lastQuantity": "1000",
        "minAmount": "100000", "maxAmount": "5000000",
        "payments": ["14"], "userId": "0", "nickName": "merchant-a",
        "side": "1", "recentOrderNum": "100", "recentExecuteRate": "99",
        "paymentPeriod": "15"
    }
    ad = src.parse(item, "BUY", "USDT", "VND", datetime.now(timezone.utc))
    assert ad.advertiser_no == "merchant-a"
