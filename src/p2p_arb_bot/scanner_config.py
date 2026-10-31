"""Configuración V1: supuestos explícitos y límites conservadores."""
import json
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .domain.execution import CostPolicy, NetworkCost, RiskPolicy


def csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(dict.fromkeys(p.strip() for p in os.getenv(name, default).split(",") if p.strip()))


def decimal(name: str, default: str) -> Decimal:
    value = Decimal(os.getenv(name, default) or default)
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} debe ser finito y no negativo")
    return value


def integer(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)) or default)
    if value < minimum:
        raise ValueError(f"{name} debe ser >= {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class ScannerSettings:
    enabled: bool = True
    exchanges: tuple[str, ...] = ("Binance", "OKX", "Bybit")
    bingx_enabled: bool = False
    risk: RiskPolicy = RiskPolicy()
    costs: CostPolicy = CostPolicy()
    networks: tuple[NetworkCost, ...] = ()
    alert_min_scans: int = 3
    alert_min_seconds: int = 30
    alert_cooldown_seconds: int = 300
    telegram_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def from_env(cls) -> "ScannerSettings":
        exchanges = csv("EXCHANGES", "Binance,OKX,Bybit")
        if not exchanges or set(exchanges)-{"Binance", "OKX", "Bybit", "BingX"}:
            raise ValueError("EXCHANGES contiene un exchange desconocido")
        networks = []
        for n in json.loads(os.getenv("NETWORK_COSTS_JSON", "[]") or "[]"):
            if type(n["deposit_enabled"]) is not bool or type(n["withdraw_enabled"]) is not bool:
                raise ValueError("Estados de red deben ser booleanos JSON")
            fee, minimum = Decimal(str(n["withdraw_fee"])), Decimal(str(n["min_withdrawal"]))
            timestamp = datetime.fromisoformat(n["timestamp"])
            if not fee.is_finite() or fee < 0 or not minimum.is_finite() or minimum < 0 or timestamp.tzinfo is None or not n["network"]:
                raise ValueError("Coste de red inválido")
            networks.append(NetworkCost(n["exchange"], n["asset"], n["network"], n["deposit_enabled"], n["withdraw_enabled"], fee, minimum, timestamp))
        risk = RiskPolicy(
            min_completion_rate=decimal("MIN_COMPLETION_RATE", "0"),
            min_completed_orders=integer("MIN_COMPLETED_ORDERS", 0, 0),
            max_payment_minutes=integer("MAX_PAYMENT_MINUTES", 60, 0),
            allowed_payments=csv("PAY_METHODS"), excluded_merchants=csv("EXCLUDE_MERCHANTS"),
            max_counterparties=integer("MAX_COUNTERPARTIES", 8),
            quote_ttl_s=integer("QUOTE_TTL_S", 45),
            outlier_pct=decimal("OUTLIER_MAX_DEV_PCT", "2.5"),
            require_verified=os.getenv("MERCHANT_CHECK", "false").lower() == "true",
        )
        if risk.min_completion_rate > 100:
            raise ValueError("MIN_COMPLETION_RATE debe estar en 0..100")
        costs = CostPolicy(
            p2p_buy_pct=decimal("P2P_BUY_FEE_PCT", "0"),
            p2p_sell_pct=decimal("P2P_SELL_FEE_PCT", "0"),
            trading_pct=decimal("TRADING_FEE_PCT", "0"), tax_pct=decimal("TAX_PCT", "0"),
            safety_pct=decimal("SAFETY_BUFFER_PCT", os.getenv("FEE_BUFFER_PCT", "0.2") or "0.2"),
            counterparty_pct=decimal("COUNTERPARTY_PENALTY_PCT", "0.02"),
            network_ttl_s=integer("NETWORK_COST_TTL_S", 86400),
        )
        return cls(enabled=os.getenv("SCANNER_V1", "true").lower() == "true",
                   exchanges=exchanges, bingx_enabled=os.getenv("BINGX_ENABLED", "false").lower() == "true",
                   risk=risk, costs=costs, networks=tuple(networks),
                   alert_min_scans=integer("ALERT_MIN_SCANS", 3),
                   alert_min_seconds=integer("ALERT_MIN_SECONDS", 30, 0),
                   alert_cooldown_seconds=integer("ALERT_COOLDOWN_SECONDS", 300, 0),
                   telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                   telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""))
