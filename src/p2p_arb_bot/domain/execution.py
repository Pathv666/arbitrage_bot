"""Dimensionado por profundidad y costes: solo decisiones, sin ejecución real."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median

from .models import Ad, TradeType
from .payments import normalize_payment

ZERO = Decimal("0")
HUNDRED = Decimal("100")
EPSILON = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    # Fiabilidad de ejecución; no son indicadores AML.
    min_completion_rate: Decimal = ZERO
    min_completed_orders: int = 0
    max_payment_minutes: int = 60
    allowed_payments: tuple[str, ...] = ()
    excluded_merchants: tuple[str, ...] = ()
    max_counterparties: int = 8
    quote_ttl_s: int = 45
    outlier_pct: Decimal = Decimal("2.5")
    require_verified: bool = False


@dataclass(frozen=True, slots=True)
class Leg:
    ad: Ad
    fiat_amount: Decimal
    asset_quantity: Decimal


@dataclass(frozen=True, slots=True)
class Fill:
    side: TradeType
    total_fiat: Decimal
    total_asset: Decimal
    effective_price: Decimal
    legs: tuple[Leg, ...]
    counterparties: int
    unfilled: Decimal  # fiat para BUY; asset para SELL dimensionado en asset
    remainder_unit: str


def eligible(ads: list[Ad], side: TradeType, policy: RiskPolicy, now: datetime) -> list[Ad]:
    allowed = {normalize_payment(p) for p in policy.allowed_payments}
    result = []
    seen = set()
    for ad in ads:
        values = (ad.price, ad.surplus, ad.min_amount, ad.max_amount)
        if not all(v.is_finite() for v in values):
            continue
        if ad.trade_type != side or ad.price <= 0 or ad.surplus <= 0:
            continue
        if ad.min_amount < 0 or ad.max_amount < ad.min_amount or ad.max_amount <= 0:
            continue
        if ad.timestamp.tzinfo is None or not 0 <= (now-ad.timestamp).total_seconds() <= policy.quote_ttl_s:
            continue
        if ad.completion_rate is not None and (not ad.completion_rate.is_finite() or not 0 <= ad.completion_rate <= 100):
            continue
        if ad.completed_orders is not None and ad.completed_orders < 0:
            continue
        if ad.payment_time_minutes is not None and ad.payment_time_minutes <= 0:
            continue
        if not ad.adv_no or not ad.advertiser_no or ad.adv_no == "None" or ad.advertiser_no == "None":
            continue
        if ad.advertiser_no in policy.excluded_merchants or ad.advertiser_name in policy.excluded_merchants:
            continue
        if policy.require_verified and not ad.verified:
            continue
        if policy.min_completion_rate and (ad.completion_rate is None or not ad.completion_rate.is_finite() or ad.completion_rate < policy.min_completion_rate):
            continue
        if policy.min_completed_orders and (ad.completed_orders is None or ad.completed_orders < policy.min_completed_orders):
            continue
        if policy.max_payment_minutes and (ad.payment_time_minutes is None or ad.payment_time_minutes > policy.max_payment_minutes):
            continue
        if allowed and not allowed.intersection(normalize_payment(p) for p in ad.pay_methods):
            continue
        identity = (ad.exchange, ad.adv_no)
        if identity not in seen:
            seen.add(identity)
            result.append(ad)
    if policy.outlier_pct and len(result) >= 3:
        center = median(ad.price for ad in result)
        result = [ad for ad in result if abs(ad.price-center)/center*HUNDRED <= policy.outlier_pct]
    return sorted(result, key=lambda ad: ad.price, reverse=side == "SELL")


def blend(ads: list[Ad], side: TradeType, amount: Decimal, policy: RiskPolicy,
          now: datetime, *, asset_sized: bool = False) -> Fill:
    """Recorre ads elegibles; SELL usa el asset realmente obtenido al comprar.

    Conservador: omite un mínimo que no cabe; no busca combinaciones globales
    mediante redistribución de legs previos. Nunca presenta un parcial como completo.
    """
    if not amount.is_finite() or amount <= 0:
        raise ValueError("El tamaño debe ser finito y positivo")
    remaining = amount
    legs = []
    merchants = set()
    for ad in eligible(ads, side, policy, now):
        merchant = (ad.exchange, ad.advertiser_no)
        if merchant not in merchants and len(merchants) >= policy.max_counterparties:
            continue
        capacity = min(ad.max_amount, ad.surplus*ad.price)
        allocation = min(remaining*ad.price if asset_sized else remaining, capacity)
        if allocation < ad.min_amount:
            continue
        quantity = allocation/ad.price
        legs.append(Leg(ad, allocation, quantity))
        merchants.add(merchant)
        remaining = max(ZERO, remaining-(quantity if asset_sized else allocation))
        if remaining <= EPSILON:
            remaining = ZERO
            break
    fiat = sum((leg.fiat_amount for leg in legs), ZERO)
    asset = sum((leg.asset_quantity for leg in legs), ZERO)
    return Fill(side, fiat, asset, fiat/asset if asset else ZERO, tuple(legs),
                len(merchants), remaining, "asset" if asset_sized else "fiat")


@dataclass(frozen=True, slots=True)
class NetworkCost:
    exchange: str
    asset: str
    network: str
    deposit_enabled: bool
    withdraw_enabled: bool
    withdraw_fee: Decimal
    min_withdrawal: Decimal
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class CostPolicy:
    p2p_buy_pct: Decimal = ZERO
    p2p_sell_pct: Decimal = ZERO
    trading_pct: Decimal = ZERO
    tax_pct: Decimal = ZERO  # hipótesis sobre ingresos de venta, no asesoría fiscal
    safety_pct: Decimal = Decimal("0.2")
    counterparty_pct: Decimal = Decimal("0.02")
    network_ttl_s: int = 86400


@dataclass(frozen=True, slots=True)
class Route:
    buy_exchange: str
    sell_exchange: str
    asset: str
    fiat: str
    requested_size: Decimal
    buy: Fill
    sell: Fill
    headline_spread_pct: Decimal
    gross_spread_pct: Decimal
    executable_spread_pct: Decimal
    estimated_costs: tuple[tuple[str, Decimal], ...]  # fiat; red incluida una sola vez
    net_edge_pct: Decimal
    counterparties: int
    quote_age_s: Decimal
    network: str
    feasible: bool
    reason: str


def optimize_routes(books: dict[str, list[Ad]], *, asset: str, fiat: str,
                    size: Decimal, risk: RiskPolicy, costs: CostPolicy,
                    networks: tuple[NetworkCost, ...], now: datetime) -> list[Route]:
    """Compara todos los pares dirigidos y redes comunes habilitadas."""
    # Un snapshot nuevo deshabilitado debe invalidar el anterior habilitado.
    latest = {}
    for value in networks:
        if value.timestamp.tzinfo is None or not value.network:
            continue
        key = (value.exchange, value.asset, value.network)
        if key not in latest or value.timestamp >= latest[key].timestamp:
            latest[key] = value
    networks = tuple(latest.values())
    routes = []
    for buy_exchange, buy_book in books.items():
        for sell_exchange, sell_book in books.items():
            if buy_exchange == sell_exchange:
                continue
            buy_ads = [a for a in buy_book if a.exchange == buy_exchange and a.asset == asset and a.fiat == fiat]
            sell_ads = [a for a in sell_book if a.exchange == sell_exchange and a.asset == asset and a.fiat == fiat]
            buy = blend(buy_ads, "BUY", size, risk, now)
            matching = []
            for source in networks:
                if source.exchange != buy_exchange or source.asset != asset or not source.withdraw_enabled:
                    continue
                for destination in networks:
                    if destination.exchange == sell_exchange and destination.asset == asset and destination.network == source.network and destination.deposit_enabled:
                        if all(n.timestamp.tzinfo and 0 <= (now-n.timestamp).total_seconds() <= costs.network_ttl_s and n.withdraw_fee.is_finite() and n.withdraw_fee >= 0 and n.min_withdrawal.is_finite() and n.min_withdrawal >= 0 for n in (source, destination)):
                            matching.append(source)
            # Sin red confirmada se devuelve una ruta rechazada, nunca coste cero factible.
            for network in matching or [None]:
                fee = network.withdraw_fee if network else ZERO
                p2p_buy = buy.total_fiat*costs.p2p_buy_pct/HUNDRED
                transferred = max(ZERO, buy.total_asset-p2p_buy/buy.effective_price-fee) if buy.effective_price else ZERO
                sell = blend(sell_ads, "SELL", transferred, risk, now, asset_sized=True) if transferred > 0 else Fill("SELL", ZERO, ZERO, ZERO, (), 0, ZERO, "asset")
                parties = len({(leg.ad.exchange, leg.ad.advertiser_no) for leg in buy.legs+sell.legs})
                headline_buy = eligible(buy_ads, "BUY", risk, now)
                headline_sell = eligible(sell_ads, "SELL", risk, now)
                headline = ((headline_sell[0].price/headline_buy[0].price-1)*HUNDRED) if headline_buy and headline_sell else ZERO
                gross = (sell.effective_price/buy.effective_price-1)*HUNDRED if buy.effective_price and sell.effective_price else ZERO
                deductions = (
                    ("p2p_buy", p2p_buy/buy.effective_price*sell.effective_price if buy.effective_price else ZERO),
                    ("p2p_sell", sell.total_fiat*costs.p2p_sell_pct/HUNDRED),
                    ("trading", size*costs.trading_pct/HUNDRED),
                    ("network", fee*sell.effective_price),
                    ("tax", sell.total_fiat*costs.tax_pct/HUNDRED),
                    ("safety", size*costs.safety_pct/HUNDRED),
                    ("counterparty", size*costs.counterparty_pct*max(0, parties-2)/HUNDRED),
                )
                # Los fees de compra/red ya redujeron el asset vendido.
                extra = sum((value for key, value in deductions if key not in {"network", "p2p_buy"}), ZERO)
                net = (sell.total_fiat-size-extra)/size*HUNDRED
                reason = ""
                if buy.unfilled: reason = "liquidez de compra insuficiente"
                elif network is None: reason = "red de transferencia no confirmada o caducada"
                elif buy.total_asset-p2p_buy/buy.effective_price < network.min_withdrawal: reason = "mínimo de retiro no alcanzado"
                elif transferred <= 0: reason = "costes superan el inventario"
                elif sell.unfilled: reason = "liquidez de venta insuficiente"
                elif parties > risk.max_counterparties: reason = "demasiadas contrapartes"
                ages = [Decimal(str((now-leg.ad.timestamp).total_seconds())) for leg in buy.legs+sell.legs]
                executable = (sell.total_fiat/size-1)*HUNDRED
                routes.append(Route(buy_exchange, sell_exchange, asset, fiat, size, buy, sell,
                                    headline, gross, executable, deductions, net, parties,
                                    max(ages, default=ZERO), network.network if network else "",
                                    not reason, reason))
    return sorted(routes, key=lambda r: (r.feasible, r.net_edge_pct), reverse=True)
