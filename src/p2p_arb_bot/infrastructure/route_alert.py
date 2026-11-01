"""Presentación común de alertas V1; todos los legs y enlaces a libros públicos."""
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote, urlencode

from ..domain.execution import Route
from ..domain.models import TradeType, book_url


def market_book_url(exchange: str, side: TradeType, asset: str, fiat: str) -> str | None:
    """Lado del usuario. No fabrica deep-links a anuncios ni enlaces de trading API."""
    if exchange == "Binance":
        return book_url(trade_type=side, asset=quote(asset, safe=""), fiat=quote(fiat, safe=""))
    if exchange == "OKX":
        return (f"https://www.okx.com/p2p-markets/{quote(fiat.lower(), safe='')}/"
                f"{side.lower()}-{quote(asset.lower(), safe='')}")
    if exchange == "Bybit":
        query = urlencode({"actionType": "1" if side == "BUY" else "0",
                           "token": asset, "fiat": fiat})
        return f"https://www.bybit.com/fiat/trade/otc/?{query}"
    return None


def _number(value: Decimal) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


def route_alert_lines(route: Route, detected_at: datetime) -> tuple[str, ...]:
    """Un precio agrupado siempre aparece junto al número y detalle de sus legs."""
    lines = [
        f"Ruta P2P V1 {route.asset}/{route.fiat} · solo lectura",
        f"{route.buy_exchange} → {route.sell_exchange}",
        f"Detectada: {detected_at.isoformat()}",
        f"Tamaño solicitado: {_number(route.requested_size)} {route.fiat}",
        f"Precio efectivo agrupado compra: {_number(route.buy.effective_price)} {route.fiat}/{route.asset}",
        f"Precio efectivo agrupado venta: {_number(route.sell.effective_price)} {route.fiat}/{route.asset}",
        f"Mejor precio de venta rápida: {_number(route.fast_sell_best_price)} {route.fiat}/{route.asset}",
        f"Precio de venta rápida ejecutable: {_number(route.fast_sell_effective_price)} {route.fiat}/{route.asset}",
        f"Venta inmediata cubierta: {_number(route.fast_sell_fill_pct)}% · impacto de precio: {_number(route.fast_sell_price_impact_pct)}%",
        f"Margen neto estimado: {route.net_edge_pct:.3f}% · spread bruto: {route.gross_spread_pct:.3f}%",
        f"Red: {route.network or 'no confirmada'} · antigüedad de quotes al detectar: {_number(route.quote_age_s)} s",
        f"Contrapartes totales: {route.counterparties} · legs: {len(route.buy.legs)} compra + {len(route.sell.legs)} venta",
    ]
    for label, side, exchange, fill in (
        ("Compra", "BUY", route.buy_exchange, route.buy),
        ("Venta", "SELL", route.sell_exchange, route.sell),
    ):
        lines.append(f"{label} {exchange}: {len(fill.legs)} legs · {fill.counterparties} contrapartes")
        for index, leg in enumerate(fill.legs, 1):
            ad = leg.ad
            lines.append(
                f"  {label} leg {index}/{len(fill.legs)} · ad {ad.adv_no} · "
                f"{ad.advertiser_name} (id {ad.advertiser_no}) · "
                f"{_number(leg.fiat_amount)} {route.fiat} @ {_number(ad.price)} · "
                f"{_number(leg.asset_quantity)} {route.asset} · "
                f"pagos: {', '.join(ad.pay_methods) or 'no informados'}"
            )
        url = market_book_url(exchange, side, route.asset, route.fiat)
        lines.append(f"Libro {label.lower()}: {url or 'enlace no verificado'}")
    lines.append("Libros en vivo; no reservan anuncios. Confirma liquidez, métodos y costes antes de operar.")
    return tuple(lines)
