"""Fuentes de solo lectura; contratos públicos no oficiales sujetos a cambios."""
import asyncio
import logging
import random
import time
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .binance_p2p import BinanceP2PSource
from ..domain.models import Ad, TradeType

logger = logging.getLogger(__name__)


def number(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Número no finito")
    return result


def optional_int(value: Any) -> int | None:
    return int(value) if value is not None and value != "" else None


class PublicSource:
    exchange = ""
    endpoint = ""
    timeout_s = 8

    def __init__(self, *, impersonate="chrome", proxy=None):
        # Reutiliza sesiones/TLS/proxy del cliente existente, sin API keys.
        self.client = BinanceP2PSource(impersonate=impersonate, proxy=proxy)

    async def fetch_ads(self, *, asset, fiat, trade_type, pay_types, rows=20,
                        trans_amount="", merchant_check=False):
        method, params = self.request(asset, fiat, trade_type, min(rows, 100))
        async with self.client._new_session() as session:
            response = await session.request(method, self.endpoint,
                                            **params, timeout=self.timeout_s)
            if response.status_code != 200:
                raise RuntimeError(f"{self.exchange}: HTTP {response.status_code}")
            payload = response.json()
        records = self.records(payload, trade_type)
        if not isinstance(records, list):
            raise ValueError(f"{self.exchange}: lista ausente en respuesta")
        result = []
        now = datetime.now(timezone.utc)
        for item in records:
            try:
                ad = self.parse(item, trade_type, asset, fiat, now)
                if ad.price <= 0 or ad.surplus <= 0 or ad.min_amount < 0 or ad.max_amount < ad.min_amount:
                    raise ValueError("Límites inválidos")
                result.append(ad)
            except (ValueError, TypeError, KeyError, ArithmeticError, AttributeError):
                logger.warning("%s: anuncio malformado descartado", self.exchange)
        if records and not result:
            raise ValueError(f"{self.exchange}: ningún anuncio válido; posible cambio de esquema")
        return result

    async def aclose(self) -> None:
        await self.client.aclose()


class BinanceSource(PublicSource):
    exchange = "Binance"
    endpoint = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"

    def request(self, asset: str, fiat: str, side: TradeType, rows: int) -> tuple[str, dict]:
        # Binance search usa la perspectiva del taker; adv.tradeType es maker.
        return "POST", {"json": {"asset": asset, "fiat": fiat, "tradeType": side,
                                  "page": 1, "rows": rows, "payTypes": []}}

    def records(self, payload: dict, side: TradeType) -> list[dict]:
        if payload.get("success") is not True:
            raise ValueError("Binance: success != true")
        return payload["data"]

    def parse(self, item: dict, side: TradeType, asset: str, fiat: str, now: datetime) -> Ad:
        a, m = item["adv"], item["advertiser"]
        if a.get("tradeType") and a["tradeType"] != {"BUY": "SELL", "SELL": "BUY"}[side]:
            raise ValueError("Lado maker inesperado")
        ad = BinanceP2PSource._parse_ad(item, side)
        return replace(ad, asset=asset, fiat=fiat, timestamp=now,
                       price=number(a["price"]), surplus=number(a["surplusAmount"]),
                       min_amount=number(a["minSingleTransAmount"]), max_amount=number(a["maxSingleTransAmount"]),
                       completed_orders=optional_int(m.get("monthOrderCount")),
                       completion_rate=number(m["monthFinishRate"])*100 if m.get("monthFinishRate") is not None else None,
                       payment_time_minutes=optional_int(a.get("payTimeLimit")),
                       verified=m.get("userType") == "merchant")


class OKXSource(PublicSource):
    exchange = "OKX"
    endpoint = "https://www.okx.com/v3/c2c/tradingOrders/books"

    def request(self, asset: str, fiat: str, side: TradeType, rows: int) -> tuple[str, dict]:
        return "GET", {"params": {"quoteCurrency": fiat, "baseCurrency": asset,
                                   "side": "sell" if side == "BUY" else "buy", "paymentMethod": "all",
                                   "userType": "all", "showTrade": "false",
                                   "showFollow": "false", "showAlreadyTraded": "false",
                                   "isAbleFilter": "false", "t": int(time.time()*1000)}}

    def records(self, payload: dict, side: TradeType) -> list[dict]:
        if str(payload.get("code")) != "0":
            raise ValueError("OKX: code != 0")
        # El libro buy contiene makers compradores; el taker BUY consume sell.
        return payload["data"]["sell" if side == "BUY" else "buy"]

    def parse(self, a: dict, side: TradeType, asset: str, fiat: str, now: datetime) -> Ad:
        if a.get("side") != ("sell" if side == "BUY" else "buy"):
            raise ValueError("Lado maker inesperado")
        if a.get("baseCurrency", asset).upper() != asset or a.get("quoteCurrency", fiat).upper() != fiat:
            raise ValueError("Par inesperado")
        methods = a.get("paymentMethods", [])
        rate = a.get("completedRate")
        return Ad(str(a["id"]), number(a["price"]), number(a["availableAmount"]),
                  number(a["quoteMinAmountPerOrder"]), number(a["quoteMaxAmountPerOrder"]),
                  tuple(str(p["paymentMethod"] if isinstance(p, dict) else p) for p in methods),
                  str(a["merchantId"]), str(a.get("nickName", "")), side,
                  exchange=self.exchange, asset=asset, fiat=fiat, timestamp=now,
                  completed_orders=optional_int(a.get("completedOrderQuantity")),
                  completion_rate=number(rate)*100 if rate is not None else None,
                  payment_time_minutes=optional_int(a.get("paymentTimeoutMinutes")),
                  verified=bool(a.get("isMerchant", False)))


class BybitSource(PublicSource):
    exchange = "Bybit"
    endpoint = "https://api2.bybit.com/fiat/otc/item/online"

    def request(self, asset: str, fiat: str, side: TradeType, rows: int) -> tuple[str, dict]:
        # side es del maker: 1 vende, 0 compra.
        return "POST", {"json": {"tokenId": asset, "currencyId": fiat,
                                  "side": "1" if side == "BUY" else "0",
                                  "page": "1", "size": str(rows), "payment": []}}

    def records(self, payload: dict, side: TradeType) -> list[dict]:
        if str(payload.get("ret_code")) != "0":
            raise ValueError("Bybit: ret_code != 0")
        return payload["result"]["items"]

    def parse(self, a: dict, side: TradeType, asset: str, fiat: str, now: datetime) -> Ad:
        if str(a.get("side")) != ("1" if side == "BUY" else "0"):
            raise ValueError("Lado maker inesperado")
        return Ad(str(a["id"]), number(a["price"]), number(a["lastQuantity"]),
                  number(a["minAmount"]), number(a["maxAmount"]),
                  tuple(str(p) for p in a.get("payments", [])),
                  str(a["userId"]), str(a.get("nickName", "")), side,
                  exchange=self.exchange, asset=asset, fiat=fiat, timestamp=now,
                  completed_orders=optional_int(a.get("recentOrderNum")),
                  completion_rate=number(a["recentExecuteRate"]) if a.get("recentExecuteRate") is not None else None,
                  payment_time_minutes=optional_int(a.get("paymentPeriod")))


class BingXSource(PublicSource):
    exchange = "BingX"
    timeout_s = 3

    async def fetch_ads(self, **kwargs) -> list[Ad]:
        raise RuntimeError("BingX experimental: endpoint P2P no verificado; contrato mock únicamente")

    @staticmethod
    def parse_contract(a, side, asset, fiat, now):
        """Contrato interno para tests; NO afirma reflejar un endpoint de BingX."""
        return Ad(str(a["id"]), number(a["price"]), number(a["quantity"]),
                  number(a["min"]), number(a["max"]), tuple(a["payments"]),
                  str(a["merchant"]), str(a["merchant"]), side, exchange="BingX",
                  asset=asset, fiat=fiat, timestamp=now, datasource_type="EXPERIMENTAL_API")


class ResilientSource:
    """Límite por exchange, reintentos y circuito; errores quedan en el scanner."""
    def __init__(self, source, *, interval_s=0.4, attempts=2, cooldown_s=30):
        self.source = source
        self.exchange = source.exchange
        self.interval_s = interval_s
        self.attempts = attempts
        self.cooldown_s = cooldown_s
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.open_until = 0.0
        self.failures = 0

    async def fetch_ads(self, **kwargs) -> list[Ad]:
        async with self.lock:
            if time.monotonic() < self.open_until:
                raise RuntimeError(f"{self.exchange}: circuito abierto")
            for attempt in range(self.attempts):
                await asyncio.sleep(max(0, self.next_request-time.monotonic()))
                self.next_request = time.monotonic()+self.interval_s
                try:
                    result = await asyncio.wait_for(self.source.fetch_ads(**kwargs), timeout=self.source.timeout_s+1)
                    self.failures = 0
                    return result
                except Exception:
                    self.failures += 1
                    if attempt+1 < self.attempts:
                        await asyncio.sleep(2**attempt+random.random())
                    else:
                        if self.failures >= 2:
                            self.open_until = time.monotonic()+self.cooldown_s
                        raise

    async def aclose(self) -> None:
        await self.source.aclose()
