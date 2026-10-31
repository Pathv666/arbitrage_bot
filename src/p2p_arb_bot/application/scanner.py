"""Orquestador V1 con fallos por exchange y alertas de persistencia continua."""
import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from ..domain.execution import Route, optimize_routes
from ..domain.models import Opportunity, WatchTarget
from ..scanner_config import ScannerSettings
from .ports import MarketDataSource, NetworkCostProvider, Notifier, ScanPublisher, ScanRepository

logger = logging.getLogger(__name__)


class ScannerService:
    def __init__(self, sources: Mapping[str, MarketDataSource], repository: ScanRepository,
                 publisher: ScanPublisher, network_provider: NetworkCostProvider,
                 settings: ScannerSettings, notifiers: list[Notifier]):
        self.sources = sources
        self.repository = repository
        self.publisher = publisher
        self.network_provider = network_provider
        self.settings = settings
        self.notifiers = notifiers
        self.streaks = {}
        self.last_alert = {}

    async def run_cycle(self, target: WatchTarget) -> list[Route]:
        health = {}
        books = {}

        async def fetch_exchange(name, source):
            try:
                results = await asyncio.gather(*(
                    source.fetch_ads(asset=target.asset, fiat=target.fiat, trade_type=side,
                                     pay_types=[], rows=target.rows, merchant_check=False)
                    for side in ("BUY", "SELL")), return_exceptions=True)
                errors = [r for r in results if isinstance(r, BaseException)]
                if errors:
                    raise RuntimeError("; ".join(str(e) for e in errors))
                books[name] = results[0]+results[1]
                health[name] = {"status": "ok" if books[name] else "empty", "ads": len(books[name]),
                                "datasource": "EXPERIMENTAL_API" if name == "BingX" else "PUBLIC_WEB_ENDPOINT"}
            except Exception as exc:
                logger.warning("%s fuera de servicio: %s", name, exc)
                health[name] = {"status": "error", "reason": str(exc), "ads": 0}

        await asyncio.gather(*(fetch_exchange(n, s) for n, s in self.sources.items()))
        if not self.settings.bingx_enabled:
            health["BingX"] = {"status": "disabled", "reason": "experimental; endpoint no verificado"}
        now = datetime.now(timezone.utc)
        try:
            networks = await self.network_provider.snapshot()
        except Exception:
            logger.exception("No se pudo obtener costes de red")
            networks = ()
            health["network_costs"] = {"status": "error"}
        risk = replace(self.settings.risk, allowed_payments=target.pay_methods,
                       require_verified=target.merchant_check, outlier_pct=target.outlier_max_dev_pct)
        routes = optimize_routes(books, asset=target.asset, fiat=target.fiat, size=target.max_fiat,
                                 risk=risk, costs=self.settings.costs, networks=networks, now=now)
        scope = (target.asset, target.fiat, str(target.max_fiat))
        active = set()
        for route in routes:
            try:
                self.repository.save_route(route, now, health)
            except Exception:
                logger.exception("No se pudo guardar ruta")
            key = (*scope, route.buy_exchange, route.sell_exchange, route.network)
            if not route.feasible or route.net_edge_pct < target.threshold_pct:
                continue
            active.add(key)
            first, count = self.streaks.get(key, (now, 0))
            count += 1
            self.streaks[key] = (first, count)
            old_alert = self.last_alert.get(key)
            if count < self.settings.alert_min_scans or (now-first).total_seconds() < self.settings.alert_min_seconds:
                continue
            if old_alert and (now-old_alert).total_seconds() < self.settings.alert_cooldown_seconds:
                continue
            opp = self.as_opportunity(route, now)
            for notifier in self.notifiers:
                try:
                    await notifier.notify_opportunity(opp)
                except Exception:
                    logger.exception("Fallo aislado del notificador")
            self.last_alert[key] = now
        for key in list(self.streaks):
            if key[:3] == scope and key not in active:
                del self.streaks[key]
        try:
            await self.publisher.publish(target, routes, health)
        except Exception:
            logger.exception("No se pudo publicar estado")
        return routes

    @staticmethod
    def as_opportunity(route: Route, now: datetime) -> Opportunity:
        buy, sell = route.buy.legs[0].ad, route.sell.legs[0].ad
        return Opportunity(now, route.fiat, route.asset,
                           f"{route.buy_exchange}: {', '.join(buy.pay_methods)}",
                           f"{route.sell_exchange}: {', '.join(sell.pay_methods)}",
                           route.buy.effective_price, route.sell.effective_price,
                           route.gross_spread_pct, route.net_edge_pct, route.buy.total_asset,
                           buy.adv_no, sell.adv_no, buy.advertiser_name, sell.advertiser_name,
                           "", "", est_profit_fiat=route.requested_size*route.net_edge_pct/100,
                           est_profit_usdt=route.buy.total_asset*route.net_edge_pct/100)

    async def run_forever(self, targets: list[WatchTarget], interval_s: float) -> None:
        while True:
            for target in targets:
                try:
                    await self.run_cycle(target)
                except Exception:
                    logger.exception("Fallo de ciclo V1 %s", target.label)
            await asyncio.sleep(interval_s)
