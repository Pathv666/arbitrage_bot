"""Transiciones observadas, deduplicadas y con cooldown por clave estable."""
import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MonitorEvent:
    category: str
    key: str
    state: str
    summary: str
    timestamp: datetime


class EventMonitor:
    def __init__(self, notifiers, cooldown_s: int = 180):
        self.notifiers = [n for n in notifiers if callable(getattr(n, "notify_event", None))]
        self.cooldown_s = cooldown_s
        self.observed = {}
        self.notified = {}
        self.sent_at = {}
        self.channel_states = {}

    async def observe(self, category: str, key: str, state: str, summary: str,
                      now: datetime, *, initial: bool = False) -> None:
        identity = (category, key)
        if identity not in self.observed and not initial:
            self.notified[identity] = state
            for notifier in self.notifiers:
                self.channel_states[(id(notifier),identity)] = state
        self.observed[identity] = state
        if self.notified.get(identity) == state and all(self.channel_states.get((id(n),identity))==state for n in self.notifiers):
            return
        previous = self.sent_at.get(identity)
        if previous and (now-previous).total_seconds() < self.cooldown_s:
            return  # conserva pendiente: se reevalúa aunque no cambie el próximo scan
        event = MonitorEvent(category, key, state, summary, now)
        delivered = True
        for notifier in self.notifiers:
            channel = (id(notifier),identity)
            if self.channel_states.get(channel)==state:
                continue
            try:
                await notifier.notify_event(event)
                self.channel_states[channel]=state
            except Exception:
                delivered = False
                logger.warning("No se pudo entregar evento de monitorización")
        if delivered:
            self.notified[identity] = state
            self.sent_at[identity] = now

    async def scan(self, scope, routes, health, networks, threshold, now):
        for name, value in health.items():
            await self.observe("scanner_health", f"{scope}:{name}", value["status"],
                               f"{scope} · {name}: {value['status']}", now,
                               initial=value["status"] in {"error", "empty"})
        for name, value in health.get("network_costs", {}).get("exchanges", {}).items():
            state = value["status"]+":"+value.get("code", "ok")
            await self.observe("collector_health", name, state,
                               f"Network collector {name}: {value['status']} · {value.get('reason', 'ok')}",
                               now, initial=value["status"] == "error")
        # Consolidar redes para que cambiar la red elegida no invente una ruta nueva.
        grouped = {}
        for route in routes:
            key = f"{scope}:{route.buy_exchange}->{route.sell_exchange}"
            if key not in grouped or (route.feasible, route.net_edge_pct) > (grouped[key].feasible, grouped[key].net_edge_pct):
                grouped[key] = route
        previous = [k for c, k in self.observed if c == "route_status" and k.startswith(scope+":")]
        for key in set(previous) | set(grouped):
            route = grouped.get(key)
            state = "feasible" if route and route.feasible else "rejected"
            await self.observe("route_status", key, state, f"{key}: {state}", now)
            above = bool(route and route.feasible and route.net_edge_pct >= threshold)
            edge = f"{route.net_edge_pct:.3f}%" if route else "sin datos"
            await self.observe("spread_threshold", key, "above" if above else "below",
                               f"{key}: {'sobre' if above else 'bajo'} umbral {threshold}% · neto {edge}", now)
        latest = {}
        for n in networks:
            key = f"{n.exchange}:{n.asset}:{n.network}"
            if key not in latest or n.timestamp >= latest[key].timestamp:
                latest[key] = n
        for key, n in latest.items():
            state = f"deposit={n.deposit_enabled};withdraw={n.withdraw_enabled}"
            await self.observe("network_status", key, state, f"{key}: {state}", now)

        previous_networks = [key for category,key in self.observed if category == 'network_status']
        for key in set(previous_networks)-set(latest):
            await self.observe('network_status',key,'unknown',f'{key}: metadata no disponible',now)
