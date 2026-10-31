"""Transport SPOT solo GET; allowlist antes de firmar, sin ejecución."""
import asyncio
import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from curl_cffi.requests import AsyncSession

from ..domain.failures import CollectorError, check_api, check_http, safe_collector

BASES = {"Binance":"https://api.binance.com", "OKX":"https://openapi.okx.com", "BingX":"https://open-api.bingx.com", "Bybit":"https://api.bybit.com"}
PRIVATE_PATHS = {
    "Binance": {"/api/v3/account", "/api/v3/openOrders", "/api/v3/allOrders"},
    "OKX": {"/api/v5/asset/balances", "/api/v5/account/balance", "/api/v5/trade/orders-pending", "/api/v5/trade/orders-history"},
    "BingX": {"/openApi/spot/v1/account/balance", "/openApi/spot/v1/trade/openOrders",
              "/openApi/spot/v1/trade/historyOrders", "/openApi/spot/v1/user/commissionRate"},
}
PUBLIC_PATHS = {"Binance":set(), "OKX":set(), "BingX":{
    "/openApi/spot/v1/common/symbols", "/openApi/spot/v1/market/depth", "/openApi/spot/v1/ticker/bookTicker"}}


def official_base(exchange: str, value: str | None) -> str:
    base = value or BASES[exchange]
    valid = False
    try:
        parsed = urlsplit(base)
        domain = {"Binance":"binance.com", "OKX":"okx.com", "BingX":"bingx.com", "Bybit":"bybit.com"}[exchange]
        host = parsed.hostname or ""
        valid = (parsed.scheme == "https" and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment and parsed.port in (None,443)
                 and parsed.path in ("", "/") and (host == domain or host.endswith('.'+domain)))
    except Exception:
        pass
    if not valid:
        raise CollectorError("access_denied")
    return base.rstrip('/')


class SpotReadClient:
    def __init__(self, exchange: str, key: str = "", secret: str = "", passphrase: str = "",
                 *, base_url: str | None = None, interval_s: float = 1.05):
        self.exchange = exchange
        self.base_url = official_base(exchange, base_url)
        self._key, self._secret, self._passphrase = key, secret, passphrase
        self._lock = asyncio.Lock()
        self._next = 0.0
        self.interval_s = interval_s

    @property
    def configured(self) -> bool:
        return bool(self._key and self._secret and (self.exchange != "OKX" or self._passphrase))

    @safe_collector
    async def get(self, path: str, params: dict | None = None):
        if path not in PRIVATE_PATHS[self.exchange] | PUBLIC_PATHS[self.exchange]:
            raise CollectorError("access_denied")
        private = path in PRIVATE_PATHS[self.exchange]
        if private and not self.configured:
            raise CollectorError("not_configured")
        params = dict(params or {})
        headers = {}
        if private and self.exchange in {"Binance", "BingX"}:
            params.update(timestamp=int(time.time()*1000), recvWindow=5000)
        elif not private:
            # Opcional en symbols/depth; evita reutilizar un snapshot cacheado.
            if not path.endswith('bookTicker'):
                params['timestamp'] = int(time.time()*1000)
        query = urlencode(sorted(params.items()))
        request_path = path + ('?'+query if query else '')
        if private:
            if self.exchange == "OKX":
                stamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
                signature = base64.b64encode(hmac.new(self._secret.encode(), (stamp+'GET'+request_path).encode(), hashlib.sha256).digest()).decode()
                headers = {'OK-ACCESS-KEY':self._key,'OK-ACCESS-SIGN':signature,
                           'OK-ACCESS-TIMESTAMP':stamp,'OK-ACCESS-PASSPHRASE':self._passphrase}
            else:
                signature = hmac.new(self._secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                request_path += '&signature='+signature
                headers = {'X-MBX-APIKEY' if self.exchange=='Binance' else 'X-BX-APIKEY':self._key}
        async with self._lock:
            await asyncio.sleep(max(0, self._next-time.monotonic()))
            self._next=time.monotonic()+self.interval_s
            async with AsyncSession() as session:
                response=await session.get(self.base_url+request_path, headers=headers,
                                           timeout=8, allow_redirects=False)
        check_http(response.status_code)
        body=response.json()
        if self.exchange in {'OKX','BingX'}:
            check_api(body.get('code'))
            return body['data']
        if isinstance(body,dict) and 'code' in body:
            check_api(body['code'])
        return body
