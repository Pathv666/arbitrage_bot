"""Regresión del review: una ruta agrupada nunca se avisa como un único anuncio."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image, ImageDraw
from rich.console import Console

from p2p_arb_bot.application.scanner import ScannerService
from p2p_arb_bot.domain.execution import optimize_routes, CostPolicy
from p2p_arb_bot.domain.models import WatchTarget
from p2p_arb_bot.infrastructure.console_notifier import ConsoleNotifier
from p2p_arb_bot.infrastructure.route_alert import market_book_url, route_alert_lines
from p2p_arb_bot.infrastructure.scanner_output import ManualNetworkCosts, ScannerStatus
from p2p_arb_bot.infrastructure.screenshot_notifier import ScreenshotNotifier
from p2p_arb_bot.infrastructure.sqlite_repo import SQLiteRepository
from p2p_arb_bot.infrastructure.telegram_notifier import TelegramNotifier
from p2p_arb_bot.scanner_config import ScannerSettings
from tests.test_execution_v1 import ad, networks, RISK, D


@pytest.fixture
def golden_route():
    now = datetime.now(timezone.utc)
    buys = [ad("buy-1", cap="26000000"),
            ad("buy-2", price="25786", cap="12000000"),
            ad("buy-3", price="25804", cap="20000000")]
    sells = [ad("sell-1", "26200", cap="20000000", side="SELL", exchange="OKX"),
             ad("sell-2", "26190", side="SELL", exchange="OKX")]
    books = {"Binance": buys, "OKX": sells}
    for exchange, ads in books.items():
        books[exchange] = [replace(a, advertiser_name=f"merchant-{a.adv_no}",
                                   pay_methods=("VCB", f"method-{a.adv_no}"),
                                   timestamp=now-timedelta(seconds=3)) for a in ads]
    routes = optimize_routes(books, asset="USDT", fiat="VND", size=D("47300000"),
                             risk=RISK, costs=CostPolicy(), networks=networks(timestamp=now), now=now)
    assert routes[0].feasible
    assert len(routes[0].buy.legs) == 3 and len(routes[0].sell.legs) == 2
    return routes[0]


@pytest.fixture
def telegram_requests(monkeypatch):
    requests = []
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, *, json, timeout):
            requests.append(json)
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True})
    monkeypatch.setattr("p2p_arb_bot.infrastructure.telegram_notifier.AsyncSession", Session)
    return requests


def assert_complete_route(text):
    assert "Binance → OKX" in text
    assert "Tamaño solicitado: 47300000 VND" in text
    assert "Precio efectivo agrupado compra: 25788.98728513 VND/USDT" in text
    assert "Precio efectivo agrupado venta:" in text
    assert "Contrapartes totales: 5 · legs: 3 compra + 2 venta" in text
    assert "Compra Binance: 3 legs · 3 contrapartes" in text
    assert "Venta OKX: 2 legs · 2 contrapartes" in text
    assert "Red: TRC20" in text and "Margen neto estimado:" in text
    assert "antigüedad de quotes al detectar:" in text
    for side, count in (("buy", 3), ("sell", 2)):
        for index in range(1, count+1):
            assert f"ad {side}-{index}" in text
            assert f"merchant-{side}-{index} (id {side}-{index})" in text
            assert f"method-{side}-{index}" in text
    assert "26000000 VND @ 25785" in text
    assert "12000000 VND @ 25786" in text
    assert "9300000 VND @ 25804" in text
    assert "https://p2p.binance.com/es/trade/buy/USDT?fiat=VND&payment=all-payments" in text
    assert "https://www.okx.com/p2p-markets/vnd/sell-usdt" in text
    assert "solo lectura" in text


def test_golden_scanner_alert_preserves_all_legs_in_every_v1_channel(
    golden_route, telegram_requests, tmp_path, monkeypatch,
):
    """Ejercita scanner + notifiers reales, incluyendo gating y ficha opcional."""
    route = golden_route
    output = StringIO()
    console = ConsoleNotifier(beep=False, console=Console(file=output, width=300))
    telegram = TelegramNotifier("test-token", "test-chat")
    screenshots = ScreenshotNotifier(str(tmp_path/"shots"))
    drawn = []
    original_text = ImageDraw.ImageDraw.text
    def draw_text(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return original_text(self, xy, text, *args, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", draw_text)

    class Source:
        def __init__(self, fill): self.fill = fill
        async def fetch_ads(self, *, trade_type, **kwargs):
            return [leg.ad for leg in self.fill.legs if leg.ad.trade_type == trade_type]

    async def check():
        repo = SQLiteRepository(str(tmp_path/"history.db"))
        settings = ScannerSettings(risk=RISK, alert_min_scans=2, alert_min_seconds=0)
        scanner = ScannerService({"Binance": Source(route.buy), "OKX": Source(route.sell)}, repo,
                                 ScannerStatus(str(tmp_path/"status.json")),
                                 ManualNetworkCosts(networks()), settings,
                                 [console, telegram, screenshots])
        target = WatchTarget("USDT", "VND", (), D("47300000"), D("0.1"))
        await scanner.run_cycle(target)
        assert not telegram_requests and not output.getvalue()
        assert not (tmp_path/"shots").exists()
        await scanner.run_cycle(target)
        assert_complete_route(output.getvalue())
        assert len(telegram_requests) == 1
        assert_complete_route(telegram_requests[0]["text"])
        files = list((tmp_path/"shots").glob("*.png"))
        assert len(files) == 1
        with Image.open(files[0]) as image:
            assert_complete_route(image.info["route_alert"])
            assert image.height > 470  # altura ajustada a todos los legs
        assert "Compra leg 3/3" in "\n".join(drawn)
        assert "Venta leg 2/2" in "\n".join(drawn)
        await scanner.run_cycle(target)
        assert len(telegram_requests) == 1  # el mismo cooldown sigue activo
        repo.close()
    asyncio.run(check())


@pytest.mark.parametrize("exchange", ["Binance", "OKX", "Bybit"])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_public_book_links_preserve_taker_side_and_pair(exchange, side):
    parsed = urlparse(market_book_url(exchange, side, "USDT", "VND"))
    assert parsed.scheme == "https"
    query = parse_qs(parsed.query)
    if exchange == "Binance":
        assert f"/{side.lower()}/USDT" in parsed.path and query["fiat"] == ["VND"]
    elif exchange == "OKX":
        assert parsed.path == f"/p2p-markets/vnd/{side.lower()}-usdt"
    else:
        assert query == {"actionType": ["1" if side == "BUY" else "0"],
                         "token": ["USDT"], "fiat": ["VND"]}


def test_experimental_link_is_explicitly_unverified(golden_route):
    route = replace(golden_route, buy_exchange="BingX")
    assert market_book_url("BingX", "BUY", "USDT", "VND") is None
    assert "Libro compra: enlace no verificado" in route_alert_lines(route, datetime.now(timezone.utc))


def test_large_telegram_route_is_split_without_losing_any_leg(golden_route, telegram_requests):
    route = replace(golden_route, buy=replace(golden_route.buy, legs=tuple(
        replace(leg, ad=replace(leg.ad, advertiser_name="merchant-" + "😀"*2500))
        for leg in golden_route.buy.legs
    )))
    now = datetime.now(timezone.utc)
    asyncio.run(TelegramNotifier("test-token", "test-chat").notify_route(route, now))
    assert len(telegram_requests) > 1
    content = []
    for index, request in enumerate(telegram_requests, 1):
        text = request["text"]
        assert len(text.encode("utf-16-le"))//2 <= 4096
        header, body = text.split("\n", 1)
        assert header == f"Ruta V1 · parte {index}/{len(telegram_requests)}"
        content.append(body)
    assert "".join(content) == "\n".join(route_alert_lines(route, now))


def test_telegram_failure_redacts_token(golden_route, monkeypatch):
    class Session:
        async def __aenter__(self): raise RuntimeError("https://api.telegram.org/botSECRET/sendMessage")
        async def __aexit__(self, *args): pass
    monkeypatch.setattr("p2p_arb_bot.infrastructure.telegram_notifier.AsyncSession", Session)
    with pytest.raises(RuntimeError, match="No se pudo enviar aviso Telegram") as error:
        asyncio.run(TelegramNotifier("SECRET", "test-chat").notify_route(
            golden_route, datetime.now(timezone.utc)))
    assert "SECRET" not in str(error.value) and error.value.__suppress_context__


def test_disabled_screenshot_does_not_create_route_files(golden_route, tmp_path):
    asyncio.run(ScreenshotNotifier(str(tmp_path), enabled=False).notify_route(
        golden_route, datetime.now(timezone.utc)))
    assert not list(tmp_path.iterdir())


def test_legacy_single_ad_notifications_remain_supported(telegram_requests):
    from tests.test_screenshot_notifier import _opp
    opportunity = _opp()
    output = StringIO()
    async def check():
        await ConsoleNotifier(beep=False, console=Console(file=output, width=180)).notify_opportunity(opportunity)
        await TelegramNotifier("test-token", "test-chat").notify_opportunity(opportunity)
    asyncio.run(check())
    assert "Juan" not in telegram_requests[0]["text"]  # formato legado por método
    assert "Compra PagoMovil @ 36.5" in telegram_requests[0]["text"]
    assert "Vender a" in output.getvalue() and "Banesco" in output.getvalue()
    assert opportunity.buy_url in output.getvalue()
