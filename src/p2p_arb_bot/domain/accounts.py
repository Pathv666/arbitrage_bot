"""Snapshots de efectivo SPOT y órdenes observadas, sin valoración de P&L."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Balance:
    exchange: str
    scope: str
    asset: str
    available: Decimal
    locked: Decimal
    timestamp: datetime

    @property
    def total(self) -> Decimal:
        return self.available+self.locked


@dataclass(frozen=True, slots=True)
class ObservedOrder:
    exchange: str
    order_id: str
    symbol: str
    side: str
    status: str
    quantity: Decimal
    executed: Decimal
    timestamp: datetime
    source: str
