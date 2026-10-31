# Contratos SPOT de solo lectura

Este módulo no expone métodos POST/PUT/PATCH/DELETE. `SpotReadClient.get` acepta
solo paths GET explícitos y hosts HTTPS oficiales del exchange. No sigue redirects
con credenciales. No hay leverage, borrowing, órdenes de ejecución, cancelación,
transferencia, retiro ni modificación de permisos. URL/headers/response de error
no se incluyen en excepciones públicas; se descarta también el contexto original.

| Exchange | Balance | Órdenes |
| --- | --- | --- |
| Binance | GET `/api/v3/account` | GET `/api/v3/openOrders`, `/api/v3/allOrders` (UNIUSDT) |
| OKX | GET `/api/v5/asset/balances`, `/api/v5/account/balance` | GET `/api/v5/trade/orders-pending`, `/api/v5/trade/orders-history`, instType=SPOT |
| BingX | GET `/openApi/spot/v1/account/balance` | GET `/openApi/spot/v1/trade/openOrders`, `/openApi/spot/v1/trade/historyOrders` |

Balances: solo UNI/USDT, disponibles/bloqueados y fecha del snapshot. Ausencia de
asset significa dato desconocido, nunca cero supuesto. OKX separa funding de
trading_cash: usa cashBal y frozenBal, limita available por efectivo; no usa eq,
availEq ni P&L no realizado. Cualquier liability positiva o desconocida bloquea
el snapshot de trading_cash. Esto no audita ni habilita el modo de la cuenta.

Órdenes: pending/partial/filled/cancelled/failed se derivan únicamente del status
y cantidades informados. Un open order nunca es filled; FILLED requiere ejecución
completa confirmada en history. Desaparecer de openOrders no confirma nada.
Cobertura UNI-USDT y ventana histórica que permite la API (OKX reciente); no es un
ledger exhaustivo. Paginación acotada; datos truncados/incompletos se rechazan.

BingX: GET symbols, depth v1 y bookTicker para UNI-USDT; fee personalizado mediante
GET `/openApi/spot/v1/user/commissionRate`. No se infiere una fee gratis cuando falta
permiso Read o dato. Si la API rechaza la consulta sin permiso Trade, no se cambia
el permiso: se marca bloqueado y requiere revisión manual.

La revisión de liquidez usa solo UNI libre, redondea hacia abajo por stepSize,
camina bids, calcula VWAP/slippage, resto y fee equivalente con takerRate obtenido.
Comprueba TTL, consistencia, mínimos/máximos. El resultado es hipotético y **no**
se añade al cash disponible. Confirmar fee asset/discounts y precio al revisar.

Fuentes oficiales consultadas (API reference, no se ejecutan sus ejemplos de trading):
- https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md
- https://www.okx.com/docs-v5/en/
- https://github.com/BingX-API/api-ai-skills/blob/main/skills/spot-account/api-reference.md
- https://github.com/BingX-API/api-ai-skills/blob/main/skills/spot-market/api-reference.md
- https://github.com/BingX-API/api-ai-skills/blob/main/skills/spot-trade/api-reference.md
