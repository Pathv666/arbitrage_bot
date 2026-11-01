"""Reutiliza SQLite y publicación atómica; datos V1 separados de operaciones privadas."""
import json
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

from .status_notifier import StatusNotifier
from ..domain.execution import NetworkCost, Route
from ..domain.models import WatchTarget


def json_value(value):
    if isinstance(value, (Decimal, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise TypeError(type(value).__name__)


def route_payload(route: Route) -> dict:
    return json.loads(json.dumps(asdict(route), default=json_value))


def route_key(route: Route) -> str:
    return json.dumps([route.asset, route.fiat, str(route.requested_size),
                       route.buy_exchange, route.sell_exchange, route.network])


class ManualNetworkCosts:
    def __init__(self, values: tuple[NetworkCost, ...]) -> None:
        self.values = values

    async def snapshot(self) -> tuple[NetworkCost, ...]:
        return self.values


class ScannerStatus(StatusNotifier):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.targets = {}

    async def publish(self, target: WatchTarget, routes: list[Route], health: dict) -> None:
        payloads = [route_payload(r) for r in routes]
        feasible = [p for p in payloads if p["feasible"]]
        scope = f"{target.label}:{target.max_fiat}"
        self.targets[scope] = {
            "last_update": datetime.now(timezone.utc).isoformat(),
            "target": target.label, "requested_size": str(target.max_fiat),
            "health": health, "routes": payloads, "best_route": feasible[0] if feasible else None,
            "best_buy": min((p["buy"] for p in payloads if p["buy"]["unfilled"] == "0" and p["buy"]["legs"]), key=lambda p: Decimal(p["effective_price"]), default=None),
            "best_sell": max((p["sell"] for p in feasible), key=lambda p: Decimal(p["effective_price"]), default=None),
            "best_fast_sell": max(
                (p for p in payloads if Decimal(p["fast_sell_fill_pct"]) > 0),
                key=lambda p: (
                    Decimal(p["fast_sell_fill_pct"]),
                    Decimal(p["fast_sell_effective_price"]),
                ),
                default=None,
            ),
        }
        self._write({"scanner_v1": True, "targets": self.targets,
                     "target": target.label, "best_net_pct": float(feasible[0]["net_edge_pct"]) if feasible else None})


class PrivateAccountStatus(StatusNotifier):
    async def publish_private(self, snapshot: dict) -> None:
        # Nunca comparte balances con el status público del scanner.
        from ..domain.failures import CollectorError
        if not self._write(json.loads(json.dumps(snapshot, default=json_value))):
            raise CollectorError("unavailable")
