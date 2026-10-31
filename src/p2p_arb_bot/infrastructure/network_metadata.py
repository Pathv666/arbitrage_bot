"""Authenticated read-only network metadata collectors for exchange fees/status."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from ..domain.execution import NetworkCost

logger = logging.getLogger(__name__)


def _decimal(value, *, field: str) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field} inválido")
    return result


def _as_bool(value, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (1, "1", "true", "True", "TRUE"):
        return True
    if value in (0, "0", "false", "False", "FALSE"):
        return False
    raise ValueError(f"{field}: boolean inválido")


def canonical_network(value: str) -> str:
    raw = (value or "").upper().replace(" ", "").replace("_", "").replace("-", "")
    aliases = {
        "TRX": "TRC20", "TRON": "TRC20", "TRC20": "TRC20",
        "ETH": "ERC20", "ETHEREUM": "ERC20", "ERC20": "ERC20",
        "BSC": "BEP20", "BEP20": "BEP20", "BNBSMARTCHAINBEP20": "BEP20",
        "SOL": "SOL", "SOLANA": "SOL",
        "ARBITRUMONE": "ARBITRUM", "ARBITRUM": "ARBITRUM",
        "OPTIMISM": "OPTIMISM", "OP": "OPTIMISM",
        "POLYGON": "POLYGON", "MATIC": "POLYGON",
    }
    return aliases.get(raw, (value or "").strip().upper())


class BinanceNetworkCollector:
    exchange = "Binance"
    base_url = "https://api.binance.com"

    def __init__(self, api_key: str, api_secret: str, *, recv_window: int = 5000,
                 base_url: str | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.recv_window = recv_window
        if base_url:
            self.base_url = base_url.rstrip("/")

    async def snapshot(self, asset: str) -> tuple[NetworkCost, ...]:
        timestamp = int(time.time() * 1000)
        query = urlencode({"recvWindow": self.recv_window, "timestamp": timestamp})
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        url = f"{self.base_url}/sapi/v1/capital/config/getall?{query}&signature={signature}"
        async with AsyncSession() as session:
            response = await session.get(
                url, headers={"X-MBX-APIKEY": self.api_key}, timeout=10
            )
        if response.status_code != 200:
            raise RuntimeError(f"Binance network metadata HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Binance network metadata: respuesta inesperada")
        coin = next((x for x in payload if str(x.get("coin", "")).upper() == asset.upper()), None)
        if not coin:
            raise ValueError(f"Binance network metadata: {asset} ausente")
        now = datetime.now(timezone.utc)
        result = []
        for item in coin.get("networkList", []):
            network = canonical_network(str(item.get("network") or item.get("name") or ""))
            if not network:
                continue
            result.append(NetworkCost(
                self.exchange, asset.upper(), network,
                _as_bool(item.get("depositEnable"), field="depositEnable"), _as_bool(item.get("withdrawEnable"), field="withdrawEnable"),
                _decimal(item.get("withdrawFee", "0"), field="withdrawFee"),
                _decimal(item.get("withdrawMin", "0"), field="withdrawMin"),
                now,
            ))
        if not result:
            raise ValueError("Binance network metadata: networkList vacío")
        return tuple(result)


class OKXNetworkCollector:
    exchange = "OKX"
    base_url = "https://openapi.okx.com"

    def __init__(self, api_key: str, api_secret: str, passphrase: str,
                 *, base_url: str | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        if base_url:
            self.base_url = base_url.rstrip("/")

    @staticmethod
    def _timestamp() -> str:
        now = datetime.now(timezone.utc)
        return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    async def snapshot(self, asset: str) -> tuple[NetworkCost, ...]:
        path = f"/api/v5/asset/currencies?ccy={asset.upper()}"
        timestamp = self._timestamp()
        prehash = timestamp + "GET" + path
        signature = base64.b64encode(
            hmac.new(self.api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        ).decode()
        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
        }
        async with AsyncSession() as session:
            response = await session.get(self.base_url + path, headers=headers, timeout=10)
        if response.status_code != 200:
            raise RuntimeError(f"OKX network metadata HTTP {response.status_code}")
        payload = response.json()
        if str(payload.get("code")) != "0":
            raise RuntimeError(f"OKX network metadata code={payload.get('code')}")
        now = datetime.now(timezone.utc)
        result = []
        for item in payload.get("data", []):
            if str(item.get("ccy", "")).upper() != asset.upper():
                continue
            chain = str(item.get("chain") or "")
            raw_network = chain.split("-", 1)[1] if "-" in chain else chain
            network = canonical_network(raw_network)
            fee_raw = item.get("fee")
            if fee_raw in (None, ""):
                # Some OKX regions/accounts expose a fee range. Conservatively use
                # the maximum when a single fee is not present.
                fee_raw = item.get("maxFee")
            if fee_raw in (None, ""):
                fee_raw = item.get("minFee", "0")
            result.append(NetworkCost(
                self.exchange, asset.upper(), network,
                _as_bool(item.get("canDep"), field="canDep"), _as_bool(item.get("canWd"), field="canWd"),
                _decimal(fee_raw, field="fee"),
                _decimal(item.get("minWd", "0"), field="minWd"),
                now,
            ))
        if not result:
            raise ValueError("OKX network metadata: sin cadenas válidas")
        return tuple(result)


class BingXNetworkCollector:
    exchange = "BingX"
    base_url = "https://open-api.bingx.com"

    def __init__(self, api_key: str, api_secret: str, *, recv_window: int = 5000,
                 base_url: str | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.recv_window = recv_window
        if base_url:
            self.base_url = base_url.rstrip("/")

    async def snapshot(self, asset: str) -> tuple[NetworkCost, ...]:
        params = {
            "coin": asset.upper(),
            "recvWindow": self.recv_window,
            "timestamp": int(time.time() * 1000),
        }
        query = "&".join(
            f"{key}={params[key]}" for key in sorted(params)
        )
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        url = (
            f"{self.base_url}/openApi/wallets/v1/capital/config/getall"
            f"?{query}&signature={signature}"
        )
        async with AsyncSession() as session:
            response = await session.get(
                url,
                headers={
                    "X-BX-APIKEY": self.api_key,
                    "X-SOURCE-KEY": "BX-AI-SKILL",
                },
                timeout=10,
            )
        if response.status_code != 200:
            raise RuntimeError(f"BingX network metadata HTTP {response.status_code}")
        payload = response.json()
        if isinstance(payload, dict) and "code" in payload and int(payload.get("code", -1)) != 0:
            raise RuntimeError(
                f"BingX network metadata code={payload.get('code')}: {payload.get('msg', '')}"
            )
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(data, dict):
            data = data.get("coins") or data.get("list") or data.get("data") or []
        if not isinstance(data, list):
            raise ValueError("BingX network metadata: respuesta inesperada")
        coin = next(
            (x for x in data if str(x.get("coin", "")).upper() == asset.upper()),
            None,
        )
        if not coin:
            raise ValueError(f"BingX network metadata: {asset} ausente")
        now = datetime.now(timezone.utc)
        result = []
        for item in coin.get("networkList", []):
            network = canonical_network(
                str(item.get("network") or item.get("name") or "")
            )
            if not network:
                continue
            result.append(NetworkCost(
                self.exchange, asset.upper(), network,
                _as_bool(item.get("depositEnable"), field="depositEnable"),
                _as_bool(item.get("withdrawEnable"), field="withdrawEnable"),
                _decimal(item.get("withdrawFee", "0"), field="withdrawFee"),
                _decimal(item.get("withdrawMin", "0"), field="withdrawMin"),
                now,
            ))
        if not result:
            raise ValueError("BingX network metadata: networkList vacío")
        return tuple(result)


class BybitNetworkCollector:
    exchange = "Bybit"
    base_url = "https://api.bybit.com"

    def __init__(self, api_key: str, api_secret: str, *, recv_window: int = 5000,
                 base_url: str | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.recv_window = recv_window
        if base_url:
            self.base_url = base_url.rstrip("/")

    async def snapshot(self, asset: str) -> tuple[NetworkCost, ...]:
        timestamp = str(int(time.time() * 1000))
        query = urlencode({"coin": asset.upper()})
        sign_payload = timestamp + self.api_key + str(self.recv_window) + query
        signature = hmac.new(
            self.api_secret.encode(), sign_payload.encode(), hashlib.sha256
        ).hexdigest()
        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": str(self.recv_window),
        }
        async with AsyncSession() as session:
            response = await session.get(
                f"{self.base_url}/v5/asset/coin/query-info?{query}",
                headers=headers, timeout=10,
            )
        if response.status_code != 200:
            raise RuntimeError(f"Bybit network metadata HTTP {response.status_code}")
        payload = response.json()
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(f"Bybit network metadata retCode={payload.get('retCode')}")
        rows = payload.get("result", {}).get("rows", [])
        coin = next((x for x in rows if str(x.get("coin", "")).upper() == asset.upper()), None)
        if not coin:
            raise ValueError(f"Bybit network metadata: {asset} ausente")
        now = datetime.now(timezone.utc)
        result = []
        for item in coin.get("chains", []):
            network = canonical_network(str(item.get("chain") or item.get("chainType") or ""))
            fee = item.get("withdrawFee")
            # Empty fee means withdrawal unsupported per Bybit docs.
            withdraw_enabled = str(item.get("chainWithdraw")) == "1" and fee not in (None, "")
            result.append(NetworkCost(
                self.exchange, asset.upper(), network,
                str(item.get("chainDeposit")) == "1", withdraw_enabled,
                _decimal(fee or "0", field="withdrawFee"),
                _decimal(item.get("withdrawMin", "0"), field="withdrawMin"),
                now,
            ))
        if not result:
            raise ValueError("Bybit network metadata: chains vacío")
        return tuple(result)


class AutoNetworkCosts:
    """Merge manual snapshots with authenticated collectors; fail-safe on errors."""

    def __init__(self, manual: tuple[NetworkCost, ...], collectors: dict[str, object],
                 *, assets: tuple[str, ...], refresh_s: int = 600):
        self.manual = manual
        self.collectors = collectors
        self.assets = assets
        self.refresh_s = refresh_s
        self._cached: tuple[NetworkCost, ...] = ()
        self._last_refresh = 0.0
        self.health: dict[str, dict] = {}

    async def snapshot(self) -> tuple[NetworkCost, ...]:
        now_mono = time.monotonic()
        if self._cached and now_mono - self._last_refresh < self.refresh_s:
            return self._merge(self.manual, self._cached)

        values: list[NetworkCost] = []
        health: dict[str, dict] = {}

        async def fetch(exchange: str, collector):
            exchange_values = []
            try:
                for asset in self.assets:
                    exchange_values.extend(await collector.snapshot(asset))
                values.extend(exchange_values)
                health[exchange] = {
                    "status": "ok", "networks": len(exchange_values),
                    "source": "AUTHENTICATED_READ_ONLY_API",
                }
            except Exception as exc:
                logger.warning("%s network metadata failed: %s", exchange, exc)
                health[exchange] = {
                    "status": "error", "reason": str(exc),
                    "source": "AUTHENTICATED_READ_ONLY_API",
                }

        await asyncio.gather(*(fetch(name, c) for name, c in self.collectors.items()))
        self.health = health
        # Critical fail-safe: do not keep a stale authenticated cache after a
        # refresh failure. Manual snapshots may still be used if explicitly set.
        self._cached = tuple(values)
        self._last_refresh = now_mono
        return self._merge(self.manual, self._cached)

    @staticmethod
    def _merge(manual: tuple[NetworkCost, ...], dynamic: tuple[NetworkCost, ...]) -> tuple[NetworkCost, ...]:
        latest = {}
        for item in manual + dynamic:
            key = (item.exchange, item.asset, item.network)
            if key not in latest or item.timestamp >= latest[key].timestamp:
                latest[key] = item
        return tuple(latest.values())
