"""Cambios de estado y secretos hostiles: nada de excepciones crudas en salidas."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from p2p_arb_bot.application.events import EventMonitor
from p2p_arb_bot.domain.failures import CollectorError, check_http, check_api, safe_collector
from p2p_arb_bot.infrastructure import network_metadata as nm
from tests.test_execution_v1 import optimize, networks


class Sink:
    def __init__(self): self.events = []
    async def notify_event(self, event): self.events.append(event)


def test_events_dedup_cooldown_pending_and_retry():
    async def run():
        sink=Sink(); monitor=EventMonitor([sink], cooldown_s=30)
        now=datetime.now(timezone.utc)
        await monitor.observe("health", "BingX", "ok", "ok", now)
        await monitor.observe("health", "BingX", "error", "error", now)
        await monitor.observe("health", "BingX", "error", "error", now+timedelta(seconds=1))
        await monitor.observe("health", "BingX", "ok", "recovery", now+timedelta(seconds=2))
        assert [e.state for e in sink.events] == ["error"]
        await monitor.observe("health", "BingX", "ok", "recovery", now+timedelta(seconds=31))
        assert [e.state for e in sink.events] == ["error", "ok"]
    asyncio.run(run())


def test_scan_route_threshold_network_and_collector_transitions():
    async def run():
        sink=Sink(); monitor=EventMonitor([sink], cooldown_s=0)
        now=datetime.now(timezone.utc); route=optimize()[0]
        healthy={"Binance":{"status":"ok"}, "network_costs":{"status":"ok", "exchanges":{"OKX":{"status":"ok"}}}}
        await monitor.scan("scope", [route], healthy, networks(), Decimal("0.1"), now)
        failed={"Binance":{"status":"error"}, "network_costs":{"status":"empty", "exchanges":{"OKX":{"status":"error", "code":"timeout", "reason":"Timeout"}}}}
        await monitor.scan("scope", [replace(route, feasible=False)], failed,
                           tuple(replace(n, withdraw_enabled=False) for n in networks()), Decimal("0.1"), now)
        await monitor.scan("scope", [route], healthy, networks(), Decimal("0.1"), now)
        by_category={c:[e.state for e in sink.events if e.category==c] for c in {e.category for e in sink.events}}
        assert by_category["route_status"] == ["rejected", "feasible"]
        assert by_category["spread_threshold"] == ["below", "above"]
        assert by_category["collector_health"] == ["error:timeout", "ok:ok"]
        assert len(by_category["network_status"]) == 6
        await monitor.scan("scope", [], failed, (), Decimal("0.1"), now)
        assert any(e.category=="route_status" and e.state=="rejected" for e in sink.events)
        assert len([e for e in sink.events if e.category=="network_status" and e.state=="unknown"])==3
    asyncio.run(run())


def test_safe_collector_drops_raw_exception_and_context():
    @safe_collector
    async def unsafe():
        raise RuntimeError("credential-sentinel?signature=private-sentinel")
    with pytest.raises(CollectorError) as error:
        asyncio.run(unsafe())
    assert "sentinel" not in str(error.value)
    assert error.value.__context__ is None


@pytest.mark.parametrize("status,code", [(401,"authentication"),(403,"access_denied"),(429,"rate_limit"),(503,"http_error")])
def test_http_diagnostic_codes(status,code):
    with pytest.raises(CollectorError) as error: check_http(status)
    assert error.value.code==code


def test_api_message_is_never_echoed():
    with pytest.raises(CollectorError) as error: check_api("raw-secret-sentinel")
    assert "sentinel" not in str(error.value)


def test_network_error_fail_closed_manual_not_reused_and_no_leak(caplog):
    class Broken:
        async def snapshot(self, asset): raise RuntimeError("raw-private-sentinel")
    provider=nm.AutoNetworkCosts(networks(), {"Binance":Broken()}, assets=("USDT",))
    result=asyncio.run(provider.snapshot())
    assert not any(n.exchange=="Binance" for n in result)
    assert provider.health["Binance"]["code"]=="unavailable"
    assert "sentinel" not in json.dumps(provider.health)+caplog.text


def test_missing_fee_rejects_entire_network_snapshot(monkeypatch):
    from tests.test_network_metadata import FakeSession
    monkeypatch.setattr(nm,"AsyncSession",FakeSession)
    FakeSession.payload=[{"coin":"USDT","networkList":[{"network":"TRX","depositEnable":True,"withdrawEnable":True,"withdrawMin":"10"}]}]
    with pytest.raises(CollectorError) as error:
        asyncio.run(nm.BinanceNetworkCollector("fixture","fixture").snapshot("USDT"))
    assert error.value.code=="schema"


def test_successful_console_does_not_hide_failed_telegram_and_retry_is_per_channel():
    class Flaky(Sink):
        fail=True
        async def notify_event(self,event):
            if self.fail: raise RuntimeError('fixture transport unavailable')
            self.events.append(event)
    async def run():
        console=Sink(); telegram=Flaky(); monitor=EventMonitor([console,telegram],cooldown_s=0)
        now=datetime.now(timezone.utc)
        await monitor.observe('health','BingX','ok','ok',now)
        await monitor.observe('health','BingX','error','error',now)
        assert len(console.events)==1 and not telegram.events
        telegram.fail=False
        await monitor.observe('health','BingX','error','error',now)
        assert len(console.events)==1 and len(telegram.events)==1
        await monitor.observe('health','BingX','error','error',now)
        assert len(telegram.events)==1
    asyncio.run(run())
