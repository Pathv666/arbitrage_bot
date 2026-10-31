# Scanner P2P VND/USDT multi-exchange · V1

Scanner y motor de decisiones **solo lectura**. VND y USDT son los valores por
omisión para instalaciones nuevas. Un `.env` existente conserva sus valores:
actualiza `FIAT=VND` y `MAX_FIAT=47300000` para el ejemplo de Vietnam.
No crea órdenes P2P, transfiere fondos bancarios, marca pagos, libera cripto,
retira cripto ni automatiza botones de trading. No necesita API keys.

## Arquitectura y compatibilidad

- `domain/models.py` conserva `Ad` y añade exchange, par, fiabilidad, plazo,
  timestamp y procedencia. `domain/execution.py` calcula fills y compara todos
  los pares dirigidos entre exchanges, para cada red compartida confirmada.
- `application/scanner.py` consulta exchanges en paralelo, aísla errores,
  persiste decisiones y exige continuidad antes de avisar. Usa puertos de datos,
  repositorio, publicación y `NetworkCostProvider`; hoy se inyecta un snapshot manual.
- `infrastructure/exchanges.py` reutiliza sesiones, proxy y TLS del cliente Binance;
  tiene rate limit por exchange, backoff exponencial con jitter y circuito temporal.
  `SQLiteRepository` conserva tablas antiguas y añade `route_history` con UPSERT:
  first/last seen, seen count, precios exactos, legs, costes, estado y health.
- Se reutilizan consola, screenshots opcionales, escritura atómica de status y
  FastAPI/HTMX. El dashboard añade health, precios efectivos según tamaño, rutas,
  edad de quotes, legs y últimas decisiones. La base privada de operaciones y su
  autenticación permanecen separadas.
- `SCANNER_V1=true` es el runtime por omisión. `SCANNER_V1=false` selecciona el
  monitor Binance anterior, también de solo lectura. Las tablas/chart/formularios
  antiguos siguen mostrando datos del monitor legado; el panel V1 tiene su historial.

## Fuentes de datos

| Exchange | Fuente | Dirección del lado | Estado |
| --- | --- | --- | --- |
| Binance | PUBLIC_WEB_ENDPOINT | search usa lado taker; adv usa maker opuesto | Implementado |
| OKX | PUBLIC_WEB_ENDPOINT | query y libro usan maker opuesto al taker | Implementado |
| Bybit | PUBLIC_WEB_ENDPOINT | maker 1=SELL para taker BUY; maker 0=BUY para taker SELL | Implementado |
| BingX | EXPERIMENTAL_API | Solo contrato interno de pruebas | Desactivado por omisión |

Los endpoints web no son contratos oficiales garantizados. HTTP 403, bloqueos
regionales y cambios de esquema pueden impedir su uso. Una respuesta inválida
no genera liquidez. Los anuncios se consultan de nuevo en cada barrido; no se usa
caché del último precio cuando falla una fuente. La edad mide recepción local,
no promete conocer cuándo el comerciante actualizó el anuncio.

BingX aún no tiene endpoint P2P verificado. Con `BINGX_ENABLED=true` y BingX en
`EXCHANGES`, informa error experimental aislado con timeout corto; no necesita
BingX para arrancar. No se inventan datos. Los tests usan un contrato mock interno,
que **no** se presenta como esquema real de BingX.

## Dimensionado y costes

Para comprar 47.300.000 VND, los legs 26m @ 25.785, 12m @ 25.786 y
9,3m @ 25.804 producen:

```text
USDT = 26000000/25785 + 12000000/25786 + 9300000/25804
precio efectivo = 47300000 / USDT ≈ 25788,987285 VND/USDT
```

Se respetan mínimos, máximos, inventario, pagos permitidos, fiabilidad, exclusiones,
frescura y máximo de contrapartes. Los anuncios duplicados no duplican inventario.
El algoritmo recorre por precio: si un mínimo no cabe en el restante, lo omite.
Es conservador y no redistribuye legs previos para resolver un óptimo combinatorio;
una ruta parcial queda rechazada explícitamente. La búsqueda usa la profundidad
recibida (Binance/Bybit `ROWS`; OKX devuelve su snapshot público, sin paginación).

La venta se dimensiona por el asset comprado, menos fee P2P de compra (supuesto:
cobrado en asset), menos retiro de la red seleccionada. Nunca se supone que
comprar y vender la misma cantidad fiat cierra el inventario. `unfilled` de BUY
está en fiat; el de SELL en asset. Se distinguen headline, spread bruto de precios
agrupados, spread ejecutable tras transferencia, costes y neto final. P2P de venta
se aplica a ingresos; trading y buffer al capital; impuesto es una hipótesis sobre
ingresos de venta. No hay conversión spot en V1; trading fee permite reservar un
coste adicional. La penalización de contrapartes es un descuento conservador,
no un fee real. Red/fee de compra reducen asset y no se restan dos veces del neto.

`NETWORK_COSTS_JSON` contiene registros por exchange/asset/network: estados de
depósito/retiro, fee, mínimo y timestamp con zona. Debe haber retiro habilitado en
origen y depósito habilitado en destino en la misma red, con ambos registros vigentes.
Se comparan todas las redes compartidas. Sin coste confirmado y vigente, la ruta
queda rechazada. Actualiza manualmente el snapshot tras verificar fees y estados;
no se consulta ninguna API privada de retiros. Valores de `.env.example` son ejemplos,
no cotizaciones reales. `NetworkCostProvider.snapshot()` permite otro proveedor futuro.

El registro local normaliza VCB, TCB, MB, BIDV, Vietinbank, ACB, VPBank, TPBank,
Sacombank, Agribank, VIB y HDBank. Métodos genéricos y códigos Bybit desconocidos
se conservan sin inferir un banco. Si permites solo VCB, un anuncio con `BANK` o
`493` no se acepta como VCB sin mapping verificado. No hay conexión bancaria.
Los ratios de finalización solo son fiabilidad de ejecución. Riesgo operativo
(contrapartes, tiempos, red) y exposición bancaria (entidades/métodos seleccionados)
requieren evaluación humana; no se calcula ninguna puntuación AML.

## Ejecución local y configuración

### Windows / laptop local-first

Para Windows 10/11, usa el runbook y scripts PowerShell preparados para validar V1 antes de VPS:

- [docs/LOCAL_WINDOWS.md](docs/LOCAL_WINDOWS.md)
- `scripts/windows/setup.ps1`
- `scripts/windows/smoke.ps1`
- `scripts/windows/run_scanner.ps1`
- `scripts/windows/run_dashboard.ps1`

El workflow VPS está en modo **manual-only** durante esta fase; los pushes a `main` no deben desplegar V1 automáticamente.

```bash
make venv
make install
make env
# Editar .env: VND, tamaño, redes verificadas y filtros
NO_INPUT=true make run
make dashboard
make test
# Smoke opcional solo lectura; requiere acceso a endpoints de mercado
venv/bin/python scripts/smoke_endpoints.py
```

Toda configuración está documentada en `.env.example`. `Defaults.from_env()`
respeta `ENV_PATH`. Principales grupos:

- Par y tamaño: `ASSETS`, `FIAT`, `MAX_FIAT`, `PAY_METHODS`, `ROWS`.
- Runtime: `SCANNER_V1`, `EXCHANGES`, `BINGX_ENABLED`, `POLL_INTERVAL_S`, `PROXY`,
  `IMPERSONATE`, `DB_PATH`, `STATUS_PATH`, `LOG_PATH`, `SCREENSHOTS`, `BEEP`.
- Fiabilidad: `MIN_COMPLETION_RATE` (0..100), `MIN_COMPLETED_ORDERS`,
  `MAX_PAYMENT_MINUTES` (0 desactiva), `MERCHANT_CHECK`, `EXCLUDE_MERCHANTS`,
  `MAX_COUNTERPARTIES`, `QUOTE_TTL_S`, `OUTLIER_MAX_DEV_PCT`.
  Un dato desconocido falla cualquier filtro que lo requiera.
- Costes: `P2P_BUY_FEE_PCT`, `P2P_SELL_FEE_PCT`, `TRADING_FEE_PCT`, `TAX_PCT`,
  `SAFETY_BUFFER_PCT` (alias fallback `FEE_BUFFER_PCT`),
  `COUNTERPARTY_PENALTY_PCT`, `NETWORK_COSTS_JSON`, `NETWORK_COST_TTL_S`.
- Alertas: `THRESHOLD_PCT`, `ALERT_MIN_SCANS`, `ALERT_MIN_SECONDS`,
  `ALERT_COOLDOWN_SECONDS`. Un ciclo sin ruta rentable reinicia su continuidad;
  reiniciar proceso también reinicia el gating. Telegram opcional usa
  `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`; requiere egress a `api.telegram.org`.
- Los avisos V1 de consola, Telegram y screenshots reciben la ruta completa,
  sin convertirla a un anuncio único. Incluyen exchanges, tamaño solicitado,
  precios efectivos agrupados, todos los legs con importes, comerciantes y pagos,
  contrapartes totales, red, margen neto y edad de quotes al detectar. Los enlaces
  abren libros públicos de Binance/OKX/Bybit en el lado del usuario; no reservan
  ni ejecutan anuncios. BingX indica enlace no verificado. Telegram divide avisos
  largos en partes numeradas sin omitir legs; las fichas PNG ajustan su altura.
- Dashboard y autenticación: `DASHBOARD_*`, `ENV_PATH`, `BOT_PIDFILE`, `TRADES_DB_PATH`.

Los timestamps mostrados son ISO con zona; no se oculta la antigüedad del último
barrido si el bot deja de actualizar. El historial agrega cada ruta/tamaño/red:
`seen_count` cuenta observaciones, no certifica rentabilidad continua. Las alertas
usan un contador de continuidad independiente. No hay linter ni type checker
configurados; `make lint` no está implementado. No se introduce tooling nuevo.

## Validación manual y siguiente fase

1. Ejecutar tests y smoke; comprobar ambos lados y métodos reales en el libro P2P.
2. Cargar snapshots de red vigentes para ambos exchanges y verificar en dashboard
   precios agrupados, inventario final, legs y causa de cada rechazo.
3. Desactivar temporalmente una fuente: las demás deben seguir actualizando.
4. Comprobar tres scans consecutivos y duración mínima antes de recibir una alerta.
5. Habilitar BingX solo para comprobar degradación hasta verificar un endpoint real.

Siguiente fase recomendada: fixtures sanitizados de cambios de esquema, mapping
verificado de pagos por exchange, paginación, actualización dinámica de costes y
validación observacional de oportunidades. Mantener cualquier ejecución de fondos
fuera del scanner y su runtime.

---

## Documentación del monitor Binance legado

La documentación histórica siguiente corresponde a `SCANNER_V1=false` y conserva
sus ejemplos de otras monedas. Para V1 prevalecen los valores anteriores.

# P2P Arb Bot — Monitor de arbitraje Binance P2P (USDT/VES)

Bot en Python que **monitorea** el mercado P2P de Binance para un par (por
defecto USDT/VES), detecta oportunidades de arbitraje (spread entre el mejor
precio de compra y el mejor de venta) según los métodos de pago y el monto en
USDT que indiques, **avisa por consola** cuando la ganancia neta supera un
umbral, y guarda cada oportunidad en **SQLite** con el % de ganancia y los
enlaces a los anuncios.

> ⚠️ **El bot SOLO monitorea y avisa.** No ejecuta operaciones, no publica
> anuncios y **no** usa API keys de trading de Binance. Usa únicamente el
> endpoint público de búsqueda de anuncios.

![Dashboard del bot: estado, KPIs de 24 h, monto a usar en la operación y tabla de oportunidades recientes](docs/dashboard.png)

*Instancia en vivo: <https://arbitragebot.automateca.site/> (supervisión pública;
arrancar, parar y configurar exigen login).*

## Arquitectura

Arquitectura limpia con dependencias apuntando hacia el dominio:

![Capas del proyecto: domain, application, infrastructure y main.py como composition root](docs/arquitectura.png)

```
src/p2p_arb_bot/
├── domain/          # Modelos puros + lógica de arbitraje (sin I/O, testeable)
│   ├── models.py
│   └── arbitrage.py
├── application/     # Casos de uso + puertos (Protocols)
│   ├── ports.py     # MarketDataSource, OpportunityRepository, Notifier
│   └── monitor.py   # MonitorService: buscar → analizar → persistir → notificar
├── infrastructure/  # Adaptadores concretos
│   ├── binance_p2p.py     # MarketDataSource con curl_cffi (async, impersonation TLS)
│   ├── discovery.py       # Descubre métodos de pago disponibles
│   ├── sqlite_repo.py     # OpportunityRepository (SQLite)
│   └── console_notifier.py# Notifier (rich)
├── config.py        # Configuración tipada + carga .env
└── main.py          # Composition root: wiring, CLI, loop, cierre limpio
```

La inversión de dependencias (puertos con `typing.Protocol` + inyección por
constructor) permite cambiar piezas sin tocar el núcleo: otra fuente de datos
(Bybit/OKX), otro repositorio (Postgres/CSV) u otro notificador (Telegram,
webhook). Notificadores y repositorios se pasan como **listas**, así puedes
combinar varios a la vez. La configuración admite una lista de *watch targets*,
de modo que escalar a multi-par / multi-fiat no requiere cambios estructurales.

## Instalación

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e .          # registra el paquete (src/) y el comando p2p-arb-bot
```

Requiere Python 3.11+. El `pip install -e .` es lo que permite ejecutar
`python -m p2p_arb_bot.main`; sin él, el paquete vive en `src/` y Python no lo
encuentra. (Alternativa sin instalar: `PYTHONPATH=src python -m p2p_arb_bot.main`.)

## Uso

```bash
cp .env.example .env   # opcional: ajusta valores por defecto
python -m p2p_arb_bot.main
```

Al arrancar, el bot:

1. Descubre los métodos de pago disponibles para el par y los muestra numerados.
2. Te pide elegir uno o varios (Enter = todos), el monto máximo en USDT, el
   umbral de ganancia %, el buffer de fees %, el intervalo de polling y si
   filtrar solo comerciantes verificados. Cada prompt usa el valor del `.env`
   como predeterminado (Enter para aceptarlo).
3. Entra en bucle: consulta ambos lados (BUY/SELL) en paralelo, calcula el
   mejor spread (incluyendo **pares cruzados** entre métodos), y:
   - Si hay oportunidad ≥ umbral: imprime un panel destacado con % neto/bruto,
     precios, métodos y los **dos enlaces a las publicaciones** (compra y venta),
     y la guarda en SQLite.
   - Si no: imprime una línea de estado discreta (heartbeat) con el mejor
     spread actual.

Para correr **sin interacción** (cron/servidor), pon `NO_INPUT=true` en `.env`
y define el resto de variables.

`Ctrl+C` cierra la conexión HTTP y la base de datos de forma limpia.

## Dashboard web

Un dashboard (FastAPI + HTMX) permite **supervisar, ejecutar y configurar** el bot
desde el navegador. La **supervisión es pública** (no requiere login); **arrancar /
parar el bot y editar la configuración exigen contraseña**.

```bash
make dashboard      # local: http://localhost:8000
```

Configura antes en `.env`:

- `DASHBOARD_PASSWORD` — contraseña para las acciones protegidas (sin ella, el
  dashboard queda en modo solo-lectura).
- `DASHBOARD_SECRET` — secreto para firmar la cookie de sesión
  (`python -c "import secrets; print(secrets.token_hex(32))"`).
- `DASHBOARD_HOST` / `DASHBOARD_PORT` — interfaz y puerto (por defecto `0.0.0.0:8000`).

Qué ofrece:

- **Supervisar** (público): estado del bot (corriendo/detenido + uptime), mejor
  spread neto actual, oportunidades de las últimas 24 h, profit estimado, tabla de
  oportunidades recientes y las últimas líneas del log. Todo se auto-refresca.
- **Últimas 24 horas** (público): gráfica con la cantidad de detecciones y el
  horario de cada intervalo de una hora, desde 24 horas antes del momento actual,
  en horario de Venezuela (UTC−4). Se actualiza cada 30 s
  y cuenta todos los registros guardados, incluidas nuevas detecciones de una
  oportunidad persistente. Limpiar las oportunidades también vacía este historial;
  las horas sin registros pueden corresponder al monitor apagado.
- **Ejecutar** (login): botones de arrancar / parar / reiniciar. El dashboard
  gestiona el bot como subproceso.
- **Configurar** (login): edita los parámetros clave (umbral %, monto máx USDT,
  métodos de pago, intervalo) y los aplica reiniciando el bot. Escribe en `.env`
  preservando el resto de claves.

El estado en vivo lo alimenta un `StatusNotifier` que el bot añade a su lista de
notifiers: vuelca el último heartbeat/oportunidad a `STATUS_PATH` (`status.json`),
así el dashboard no necesita pegarle a Binance por su cuenta.

En Docker el dashboard es el **contenedor principal** y arranca el bot como
subproceso (ver más abajo).

### Fichas de oportunidad (opcional)

Las oportunidades P2P son efímeras: cuando abres el enlace, los anuncios que las
producían ya suelen haber expirado. Con `SCREENSHOTS=true` el bot dibuja con Pillow
—sin navegador— una ficha PNG en `SCREENSHOTS_DIR` en el instante de la detección,
con las dos piernas de la operación y la aritmética del spread:

![Ficha PNG generada por el bot: pierna de compra, pierna de venta, advNo, enlaces y spread](docs/ficha-oportunidad.png)

Está **apagado por defecto** (es una función de desarrollo: genera un PNG por
oportunidad y llena el disco si el umbral es bajo).

## Despliegue con Docker

Requiere Docker con el plugin Compose. El contenedor corre el **dashboard web**
(`http://localhost:8000`), que arranca/para el bot como subproceso en modo **no
interactivo** (`NO_INPUT=true`). La configuración se toma de variables de entorno /
`.env`. La base de datos, los logs y el estado se guardan en un volumen
(`arb-data`, montado en `/data`) que persiste entre reinicios; el `.env` se
bind-montea para que la edición desde el dashboard persista.

```bash
make env            # crea .env desde .env.example (ajústalo a tu gusto)
make docker-up      # construye y levanta el dashboard en segundo plano
make docker-logs    # sigue la salida
make docker-down    # detiene y elimina el contenedor
```

Luego abre `http://localhost:8000` y arranca el bot desde el dashboard (necesitas
`DASHBOARD_PASSWORD` en `.env`).

O directamente con Compose:

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs -f
```

En modo no interactivo no hay menú de métodos: define `PAY_METHODS` en `.env`
(vacío = vigilar todos) junto con `MAX_USDT`, `THRESHOLD_PCT`, etc. Para
**descubrir métodos** o configurar a mano dentro del contenedor, lánzalo
interactivo:

```bash
make docker-run     # corre el bot interactivo dentro del contenedor del dashboard
```

El contenedor necesita **salida a Internet** hacia `p2p.binance.com`. Si tu red
de Docker la bloquea o Binance limita la IP, define `PROXY` en `.env` (se respeta
dentro del contenedor) o ajusta la red del host. Errores `curl: (28) timed out`
en los logs indican precisamente falta de egress.

### Makefile

`make` (o `make help`) lista todos los objetivos: `install`, `test`, `run`,
`dashboard`, `clean`, `env`, y los `docker-*` (`docker-build`, `docker-up`,
`docker-down`, `docker-logs`, `docker-run`, `docker-shell`).

### Terminología

- `BUY`  → anuncios donde **tú compras** USDT (precio que pagas). El mejor es el
  **más bajo**.
- `SELL` → anuncios donde **tú vendes** USDT (precio que recibes). El mejor es el
  **más alto**.

Hay oportunidad cuando `best_sell > best_buy` y `net_pct ≥ umbral`, con
`net_pct = spread_pct − fee_buffer_pct`.

### Interpretar el heartbeat (`sin datos / sin pares elegibles`)

Cuando **no** hay oportunidad, el bot imprime una línea de estado (heartbeat) en
lugar de un panel. Puede decir dos cosas:

- **`mejor spread neto X%`** — sí encontró al menos una pareja compra/venta
  elegible, pero el mejor spread quedó por debajo de tu umbral. Es lo normal la
  mayor parte del tiempo.
- **`sin datos / sin pares elegibles`** — en ese ciclo **no encontró ni una sola
  pareja compra-venta con la que calcular un spread**. No es un error: el bot está
  avisando honestamente de que no había nada que comparar.

Para poder comparar, el bot necesita **al menos un anuncio de compra Y uno de
venta que sobrevivan a estos 4 filtros** (`domain/arbitrage.py`, función
`_eligible`). Si **cualquiera de los dos lados** queda vacío, no hay par →
`sin datos / sin pares elegibles`:

1. **Método de pago** — que el anuncio use uno de tus `PAY_METHODS`
   (vacío = se aceptan todos).
2. **Acepta tu monto** (`accepts_amount`) — que tu operación quepa entre el
   mín/máx del anuncio. El monto fiat se calcula como `MAX_USDT × precio`
   (p. ej. `100 USDT × 825 ≈ 82.500 VES`), así que un `MAX_USDT` bajo puede no
   encajar en anuncios con mínimos altos, y uno alto puede pasarse del máximo.
3. **Inventario** (`has_inventory`) — que al anunciante le queden ≥ `MAX_USDT`
   disponibles (`surplusAmount`).
4. **Outliers** — que el precio no se desvíe de la mediana más de
   `OUTLIER_MAX_DEV_PCT` (descarta precios "cebo"). `0` desactiva este filtro.

Si ves ese mensaje de forma persistente y **sin que se registren oportunidades**
(la tabla `opportunities` sigue vacía), casi siempre es por **configuración
demasiado restrictiva**, no por un fallo de red: revisa `MAX_USDT` (prueba un
valor que encaje con los límites típicos del par), reduce la lista de
`PAY_METHODS` o déjala vacía, y afloja/ajusta `OUTLIER_MAX_DEV_PCT`. Que el bot
traiga anuncios pero no forme pares confirma que el egress a Binance funciona; el
cuello de botella son los filtros de elegibilidad.

## Base de datos

Tabla `opportunities` (SQLite, ruta en `DB_PATH`) con `detected_at`, par,
métodos de cada pierna, precios, `spread_pct`, `net_pct`, `max_usdt`, los
`advNo` de cada anuncio, anunciantes, los enlaces a cada publicación y `est_profit_usdt`
(ganancia neta estimada en USDT = `max_usdt * net_pct / 100`). Índice por
`detected_at`. Las BDs creadas con un esquema anterior se migran solas al
arrancar (se añade la columna nueva si falta).
Se evitan duplicados idénticos (mismos `advNo` + precios) dentro de una ventana
de 60 s.

Consulta rápida:

```bash
sqlite3 opportunities.db "SELECT detected_at, net_pct, buy_price, sell_price FROM opportunities ORDER BY detected_at DESC LIMIT 10;"
```

## Configuración (.env)

Todas las variables están documentadas en [`.env.example`](.env.example). Las
más relevantes: `ASSET`, `FIAT`, `PAY_METHODS`, `MAX_USDT`, `THRESHOLD_PCT`,
`FEE_BUFFER_PCT`, `MERCHANT_CHECK`, `POLL_INTERVAL_S`, `DB_PATH`, `IMPERSONATE`,
`PROXY`, `BEEP`, `NO_INPUT`.

### Anti-bloqueo

El endpoint puede hacer fingerprinting TLS/JA3, por lo que `requests`/`httpx`
pueden bloquearse. Se usa `curl_cffi` con `impersonate="chrome"`, que replica el
handshake de Chrome sin necesidad de navegador. Hay backoff exponencial con
jitter ante 403/429/timeouts, validación de `success:true` (un 200 no basta) y
polling con jitter. Se soporta proxy opcional vía `PROXY`/`HTTPS_PROXY` (sin
credenciales en el código).

## Tests

```bash
pytest
```

Los tests cubren la capa de dominio (spread, filtrado por límites, pares
cruzados) y el caso de uso (persistencia, supresión de duplicados, heartbeats,
múltiples adaptadores) usando fakes, **sin red ni disco**.

## Licencia

MIT — ver [LICENSE](LICENSE). La fuente Liberation Sans empaquetada en
`src/p2p_arb_bot/infrastructure/fonts/` conserva su propia licencia (SIL OFL).

### Metadata de red automática

V1.1 puede refrescar fees/minimum/status de Binance, OKX y Bybit con credenciales API de solo lectura. Ver `docs/LOCAL_WINDOWS.md`.
No necesita ni debe usar permisos Trade/Withdraw.

### Observabilidad read-only

Desde `944beda`, el panel de rutas muestra diagnóstico por red y health de cada
collector. Errores públicos usan códigos cerrados; jamás incluyen mensajes de
API, URL firmada ni excepciones de transporte. Metadatos sin fee/mínimo se rechazan.
Los collectors configurados fallan cerrados: no reutilizan manual snapshots del
mismo exchange tras fallar. Telegram observa transiciones de health, red,
feasibilidad y threshold con dedup/cooldown de 180s, además de los avisos de ruta.
Véase `docs/LOCAL_WINDOWS.md` para validación sin leer el `.env` real durante tests.
