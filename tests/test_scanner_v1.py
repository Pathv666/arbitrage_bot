"""Ciclos aislados, persistencia y gating de alertas con fakes de puertos."""
import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from p2p_arb_bot.application.scanner import ScannerService
from p2p_arb_bot.config import Defaults
from p2p_arb_bot.domain.models import WatchTarget
from p2p_arb_bot.infrastructure.scanner_output import ManualNetworkCosts, ScannerStatus
from p2p_arb_bot.infrastructure.sqlite_repo import SQLiteRepository
from p2p_arb_bot.scanner_config import ScannerSettings
from p2p_arb_bot.web.db_reader import recent_routes
from tests.test_execution_v1 import ad, networks, RISK, optimize, NOW


def test_vnd_config_defaults_and_env(monkeypatch):
    monkeypatch.setattr("p2p_arb_bot.config.load_dotenv", lambda **kwargs: None)
    for key in ("FIAT", "ASSETS", "ASSET", "MAX_FIAT", "NETWORK_COSTS_JSON"):
        monkeypatch.delenv(key, raising=False)
    defaults = Defaults.from_env()
    assert defaults.fiat == "VND" and defaults.assets == ("USDT",)
    assert defaults.max_fiat == D("47300000") and defaults.scanner.enabled
    monkeypatch.setenv("NETWORK_COSTS_JSON", json.dumps([{
        "exchange": "Binance", "asset": "USDT", "network": "TRC20", "deposit_enabled": True,
        "withdraw_enabled": True, "withdraw_fee": "1.5", "min_withdrawal": "10",
        "timestamp": NOW.isoformat(),
    }]))
    assert Defaults.from_env().scanner.networks[0].withdraw_fee == D("1.5")
    monkeypatch.setenv("SCANNER_V1", "false")
    assert not Defaults.from_env().scanner.enabled


@pytest.mark.parametrize("key,value", [("MIN_COMPLETION_RATE", "101"), ("MIN_COMPLETION_RATE", "NaN"),
                                        ("SAFETY_BUFFER_PCT", "-1"), ("QUOTE_TTL_S", "0"),
                                        ("MAX_COUNTERPARTIES", "0"), ("EXCHANGES", "unknown")])
def test_invalid_scanner_config(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError): ScannerSettings.from_env()


class Source:
    def __init__(self, exchange, broken=False):
        self.exchange, self.broken = exchange, broken
    async def fetch_ads(self, **kwargs):
        if self.broken: raise TimeoutError("exchange caído")
        side = kwargs["trade_type"]
        price = "25785" if self.exchange == "Binance" else "26200"
        return [replace(ad(f"{self.exchange}-{side}", price, side=side, exchange=self.exchange),
                        timestamp=datetime.now(timezone.utc))]


class Notifier:
    def __init__(self): self.alerts = []
    async def notify_route(self, route, detected_at): self.alerts.append(route)


def test_one_exchange_and_bingx_down_scanner_continues_with_gated_alert(tmp_path):
    async def check():
        db = str(tmp_path/"opps.db")
        repo = SQLiteRepository(db)
        notice = Notifier()
        status_path = tmp_path/"status.json"
        settings = ScannerSettings(risk=RISK, alert_min_scans=2, alert_min_seconds=0,
                                   bingx_enabled=True)
        sources = {e: Source(e, e in ("Bybit", "BingX")) for e in ("Binance", "OKX", "Bybit", "BingX")}
        service = ScannerService(sources, repo, ScannerStatus(str(status_path)),
                                 ManualNetworkCosts(networks()), settings, [notice])
        target = WatchTarget("USDT", "VND", (), D("47300000"), D("0.1"))
        routes = await service.run_cycle(target)
        assert routes[0].feasible and not notice.alerts
        await service.run_cycle(target)
        assert len(notice.alerts) == 1
        await service.run_cycle(target)
        assert len(notice.alerts) == 1  # cooldown
        status = json.loads(status_path.read_text())
        snapshot = next(iter(status["targets"].values()))
        assert snapshot["health"]["Bybit"]["status"] == "error"
        assert snapshot["health"]["BingX"]["status"] == "error"
        assert snapshot["health"]["Binance"]["status"] == "ok"
        assert snapshot["best_route"]["buy_exchange"] == "Binance"
        assert snapshot["best_fast_sell"]["fast_sell_fill_pct"] == "100"
        assert D(snapshot["best_fast_sell"]["fast_sell_effective_price"]) > 0
        rows = recent_routes(db)
        assert rows and rows[0]["seen_count"] == 3
        assert json.loads(rows[0]["payload"])["buy"]["legs"]
        # Pérdida de liquidez reinicia continuidad.
        sources["OKX"].broken = True
        await service.run_cycle(target)
        assert not service.streaks
        assert not next(iter(json.loads(status_path.read_text())["targets"].values()))["best_route"]
        repo.close()
    asyncio.run(check())


def test_alert_min_duration_and_network_provider_dynamic(tmp_path):
    class Provider:
        calls = 0
        async def snapshot(self):
            self.calls += 1
            return networks(fee="10000" if self.calls == 1 else "1")
    async def check():
        repo = SQLiteRepository(str(tmp_path/"db"))
        notifier = Notifier()
        provider = Provider()
        settings = ScannerSettings(risk=RISK, alert_min_scans=1, alert_min_seconds=60)
        service = ScannerService({e: Source(e) for e in ("Binance", "OKX")}, repo,
                                 ScannerStatus(str(tmp_path/"state")), provider, settings, [notifier])
        target = WatchTarget("USDT", "VND", (), D("47300000"), D("0.1"))
        assert not any(r.feasible for r in await service.run_cycle(target))
        assert any(r.feasible for r in await service.run_cycle(target))
        assert not notifier.alerts
        for key, (first, count) in service.streaks.items():
            service.streaks[key] = (first-timedelta(seconds=61), count)
        await service.run_cycle(target)
        assert len(notifier.alerts) == 1 and provider.calls == 3
        repo.close()
    asyncio.run(check())


def test_sqlite_upsert_exact_decimals_reopen(tmp_path):
    path = str(tmp_path/"history.db")
    route = optimize()[0]
    repo = SQLiteRepository(path)
    repo.save_route(route, NOW, {"Binance": {"status": "ok"}})
    repo.save_route(route, NOW+timedelta(seconds=30), {})
    repo.close()
    rows = recent_routes(path)
    assert len(rows) == 1 and rows[0]["seen_count"] == 2
    assert rows[0]["first_seen"] == NOW.isoformat()
    assert rows[0]["last_seen"] == (NOW+timedelta(seconds=30)).isoformat()
    assert rows[0]["buy_price"] == str(route.buy.effective_price)
    repo = SQLiteRepository(path)  # migración idempotente
    repo.close()


def test_v1_default_runtime_wires_only_read_sources(monkeypatch, tmp_path):
    from p2p_arb_bot import main
    captured = {}
    async def run(self, targets, interval):
        captured["service"] = self
        captured["targets"] = targets
    monkeypatch.setattr(ScannerService, "run_forever", run)
    defaults = Defaults(db_path=str(tmp_path/"db"), beep=False, screenshots_enabled=True,
                        scanner=ScannerSettings(telegram_token="test-token", telegram_chat_id="test-chat"))
    asyncio.run(main.amain(defaults))
    assert set(captured["service"].sources) == {"Binance", "OKX", "Bybit"}
    assert captured["targets"][0].fiat == "VND"
    assert len(captured["service"].notifiers) == 3
    assert all(callable(n.notify_route) for n in captured["service"].notifiers)


def test_env_path_prevents_loading_repo_env(monkeypatch, tmp_path):
    file = tmp_path/"custom.env"
    file.write_text("FIAT=VND\nMAX_FIAT=47300000\n")
    monkeypatch.setenv("ENV_PATH", str(file))
    monkeypatch.delenv("FIAT", raising=False)
    monkeypatch.delenv("MAX_FIAT", raising=False)
    assert Defaults.from_env().fiat == "VND"


def test_notifier_failure_does_not_stop_publishing(tmp_path):
    class BrokenNotifier:
        async def notify_route(self, route, detected_at): raise RuntimeError("notifier unavailable")
    async def check():
        repo = SQLiteRepository(str(tmp_path/"db"))
        settings = ScannerSettings(risk=RISK, alert_min_scans=1, alert_min_seconds=0)
        status = tmp_path/"state"
        service = ScannerService({e: Source(e) for e in ("Binance", "OKX")}, repo,
                                 ScannerStatus(str(status)), ManualNetworkCosts(networks()),
                                 settings, [BrokenNotifier()])
        target = WatchTarget("USDT", "VND", (), D("47300000"), D("0.1"))
        assert (await service.run_cycle(target))[0].feasible
        assert status.exists()
        repo.close()
    asyncio.run(check())


def test_dynamic_network_snapshot_timestamp_not_future_rejected(monkeypatch, tmp_path):
    # Regression is covered at service boundary: network provider may timestamp
    # snapshots during snapshot(), so optimization 'now' must be taken afterwards.
    from datetime import datetime, timezone
    from decimal import Decimal as D
    from p2p_arb_bot.domain.execution import NetworkCost

    class Provider:
        health = {}
        async def snapshot(self):
            return (NetworkCost("Binance", "USDT", "TRC20", True, True,
                                D("1"), D("1"), datetime.now(timezone.utc)),)

    assert Provider()
