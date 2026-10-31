from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from p2p_arb_bot.domain.execution import NetworkCost
from p2p_arb_bot.infrastructure import network_metadata as nm


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeSession:
    payload = None
    last = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, headers=None, timeout=None):
        type(self).last = {"url": url, "headers": headers or {}, "timeout": timeout}
        return FakeResponse(type(self).payload)


def test_canonical_network_aliases():
    assert nm.canonical_network("TRX") == "TRC20"
    assert nm.canonical_network("TRON") == "TRC20"
    assert nm.canonical_network("TRC20") == "TRC20"
    assert nm.canonical_network("ETH") == "ERC20"
    assert nm.canonical_network("Arbitrum One") == "ARBITRUM"


@pytest.mark.asyncio
async def test_binance_network_collector_parses_signed_read_only_metadata(monkeypatch):
    monkeypatch.setattr(nm, "AsyncSession", FakeSession)
    monkeypatch.setattr(nm.time, "time", lambda: 1700000000.0)
    FakeSession.payload = [{
        "coin": "USDT",
        "networkList": [
            {
                "network": "TRX", "depositEnable": True, "withdrawEnable": True,
                "withdrawFee": "1.5", "withdrawMin": "10"
            },
            {
                "network": "ETH", "depositEnable": "false", "withdrawEnable": "true",
                "withdrawFee": "5", "withdrawMin": "12"
            },
        ],
    }]
    out = await nm.BinanceNetworkCollector("key", "secret").snapshot("USDT")
    assert [(x.network, x.withdraw_fee, x.min_withdrawal) for x in out] == [
        ("TRC20", Decimal("1.5"), Decimal("10")),
        ("ERC20", Decimal("5"), Decimal("12")),
    ]
    assert out[1].deposit_enabled is False and out[1].withdraw_enabled is True
    assert "signature=" in FakeSession.last["url"]
    assert FakeSession.last["headers"]["X-MBX-APIKEY"] == "key"


@pytest.mark.asyncio
async def test_okx_network_collector_parses_fee_and_conservative_range(monkeypatch):
    monkeypatch.setattr(nm, "AsyncSession", FakeSession)
    monkeypatch.setattr(nm.OKXNetworkCollector, "_timestamp", staticmethod(lambda: "2026-10-31T08:00:00.000Z"))
    FakeSession.payload = {
        "code": "0",
        "data": [
            {"ccy": "USDT", "chain": "USDT-TRC20", "canDep": True, "canWd": True,
             "fee": "1", "minWd": "2"},
            {"ccy": "USDT", "chain": "USDT-ERC20", "canDep": "false", "canWd": "true",
             "fee": "", "maxFee": "7", "minFee": "5", "minWd": "10"},
        ],
    }
    out = await nm.OKXNetworkCollector("key", "secret", "pass").snapshot("USDT")
    assert out[0].network == "TRC20" and out[0].withdraw_fee == Decimal("1")
    assert out[1].network == "ERC20" and out[1].withdraw_fee == Decimal("7")
    assert out[1].deposit_enabled is False
    headers = FakeSession.last["headers"]
    assert headers["OK-ACCESS-KEY"] == "key"
    assert headers["OK-ACCESS-PASSPHRASE"] == "pass"
    assert headers["OK-ACCESS-SIGN"]


@pytest.mark.asyncio
async def test_bybit_network_collector_parses_chain_status(monkeypatch):
    monkeypatch.setattr(nm, "AsyncSession", FakeSession)
    monkeypatch.setattr(nm.time, "time", lambda: 1700000000.0)
    FakeSession.payload = {
        "retCode": 0,
        "result": {"rows": [{
            "coin": "USDT",
            "chains": [
                {"chain": "TRX", "chainType": "Tron", "chainDeposit": "1",
                 "chainWithdraw": "1", "withdrawFee": "1", "withdrawMin": "10"},
                {"chain": "ETH", "chainType": "Ethereum", "chainDeposit": "1",
                 "chainWithdraw": "0", "withdrawFee": "", "withdrawMin": "0"},
            ],
        }]},
    }
    out = await nm.BybitNetworkCollector("key", "secret").snapshot("USDT")
    assert out[0].network == "TRC20" and out[0].withdraw_enabled
    assert out[1].network == "ERC20" and not out[1].withdraw_enabled
    assert FakeSession.last["headers"]["X-BAPI-API-KEY"] == "key"
    assert FakeSession.last["headers"]["X-BAPI-SIGN"]


class FlakyCollector:
    def __init__(self):
        self.fail = False

    async def snapshot(self, asset):
        if self.fail:
            raise RuntimeError("boom")
        return (NetworkCost("Binance", asset, "TRC20", True, True,
                            Decimal("1"), Decimal("10"), datetime.now(timezone.utc)),)


@pytest.mark.asyncio
async def test_auto_network_costs_drops_stale_dynamic_cache_after_refresh_failure():
    c = FlakyCollector()
    provider = nm.AutoNetworkCosts((), {"Binance": c}, assets=("USDT",), refresh_s=30)
    first = await provider.snapshot()
    assert first and provider.health["Binance"]["status"] == "ok"
    c.fail = True
    provider._last_refresh = -10**9
    second = await provider.snapshot()
    assert second == ()
    assert provider.health["Binance"]["status"] == "error"
