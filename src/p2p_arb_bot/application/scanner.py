"""Orquestador V1 con fallos por exchange y alertas de persistencia continua."""
import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Mapping

from ..domain.execution import Route, optimize_routes
from ..domain.models import WatchTarget
from ..scanner_config import ScannerSettings
from ..domain.failures import public_failure
from .events import EventMonitor
from .ports import MarketDataSource, NetworkCostProvider, RouteNotifier, ScanPublisher, ScanRepository

logger = logging.getLogger(__name__)


class ScannerService:
    def __init__(self, sources: Mapping[str, MarketDataSource], repository: ScanRepository,
                 publisher: ScanPublisher, network_provider: NetworkCostProvider,
                 settings: ScannerSettings, notifiers: list[RouteNotifier], account_monitor=None):
        self.sources = sources
        self.repository = repository
        self.publisher = publisher
        self.network_provider = network_provider
        self.settings = settings
        self.notifiers = notifiers
        self.streaks = {}
        self.last_alert = {}
        self.events = EventMonitor(notifiers)
        self.account_monitor = account_monitor

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
                    raise errors[0]
                books[name] = results[0]+results[1]
                health[name] = {"status": "ok" if books[name] else "empty", "ads": len(books[name]),
                                "datasource": "EXPERIMENTAL_API" if name == "BingX" else "PUBLIC_WEB_ENDPOINT"}
            except Exception as exc:
                logger.warning("%s fuera de servicio: %s", name, public_failure(exc)["code"])
                health[name] = {"status": "error", **public_failure(exc), "ads": 0}

        await asyncio.gather(*(fetch_exchange(n, s) for n, s in self.sources.items()))
        if not self.settings.bingx_enabled:
            health["BingX"] = {"status": "disabled", "reason": "experimental; endpoint no verificado"}
        try:
            networks = await self.network_provider.snapshot()
            provider_health = getattr(self.network_provider, "health", None)
            health["network_costs"] = {
                "status": "ok" if networks else "empty",
                "snapshots": len(networks),
                "exchanges": provider_health or {},
            }
        except Exception as exc:
            logger.warning("No se pudo obtener costes de red")
            networks = ()
            health["network_costs"] = {"status": "error", **public_failure(exc)}
        # Dynamic network snapshots are timestamped during snapshot(). Take the
        # optimization clock afterwards so freshly collected metadata is never
        # rejected as "from the future" by a few milliseconds.
        if self.account_monitor is not None:
            try:
                health['accounts'] = self.account_monitor.public_health()
            except Exception:
                logger.warning('Account monitor unavailable; datos privados descartados')
                health['accounts'] = {'status':'error','code':'unavailable'}
        now = datetime.now(timezone.utc)
        risk = replace(self.settings.risk, allowed_payments=target.pay_methods,
                       require_verified=target.merchant_check, outlier_pct=target.outlier_max_dev_pct)
        routes = optimize_routes(books, asset=target.asset, fiat=target.fiat, size=target.max_fiat,
                                 risk=risk, costs=self.settings.costs, networks=networks, now=now)
        scope = (target.asset, target.fiat, str(target.max_fiat))
        await self.events.scan(str(scope), routes, health, networks, target.threshold_pct, now)
        active = set()
        for route in routes:
            try:
                self.repository.save_route(route, now, health)
            except Exception:
                logger.warning("No se pudo guardar ruta")

        executable = [
            route for route in routes
            if route.feasible
            and route.fast_sell_fill_pct == 100
            and route.net_edge_pct >= target.threshold_pct
        ]
        best = max(executable, key=lambda route: route.net_edge_pct, default=None)
        if best is not None:
            key = (*scope, best.buy_exchange, best.sell_exchange, best.network)
            active.add(key)
            first, count = self.streaks.get(key, (now, 0))
            count += 1
            self.streaks[key] = (first, count)
            old_alert = self.last_alert.get(key)
            if (
                count >= self.settings.alert_min_scans
                and (now-first).total_seconds() >= self.settings.alert_min_seconds
                and not (
                    old_alert
                    and (now-old_alert).total_seconds() < self.settings.alert_cooldown_seconds
                )
            ):
                for notifier in self.notifiers:
                    try:
                        await notifier.notify_route(best, now)
                    except Exception:
                        logger.warning("Fallo aislado del notificador")
                self.last_alert[key] = now
        for key in list(self.streaks):
            if key[:3] == scope and key not in active:
                del self.streaks[key]
        try:
            await self.publisher.publish(target, routes, health)
        except Exception:
            logger.warning("No se pudo publicar estado")
        return routes

    async def run_forever(self, targets: list[WatchTarget], interval_s: float) -> None:
        while True:
            for target in targets:
                try:
                    await self.run_cycle(target)
                except Exception:
                    logger.warning("Fallo de ciclo V1 %s", target.label)
            await asyncio.sleep(interval_s)
