# Implementación V1: scanner P2P VND/USDT

## Auditoría previa y decisiones

Se conservan dominio/aplicación/infraestructura, modelo `Ad`, config/env,
sesiones curl_cffi/TLS/proxy de Binance, SQLite con migraciones, dashboard
FastAPI/HTMX, autenticación privada, consola, screenshots y publicación atómica.
La auditoría no encontró ejecución automática de fondos en el runtime existente.

Se añaden ejecución simulada por profundidad, comparación de todos los pares de
exchanges, costes por red y proveedor intercambiable, aislamiento por fuente,
fiabilidad de ejecución y gating de alertas. V1 es el runtime predeterminado;
el monitor Binance previo sigue disponible con `SCANNER_V1=false`.
No se añaden dependencias, formateadores ni herramientas de tipos.

## Archivos

Modificados:

- `.env.example`, `README.md`, `pyproject.toml`: valores VND, configuración y guía.
- `src/p2p_arb_bot/config.py`, `main.py`: carga/env y composición V1/legado.
- `src/p2p_arb_bot/domain/models.py`: metadatos comunes compatibles con `Ad` anterior.
- `src/p2p_arb_bot/application/ports.py`: publicación, repositorio y costes de red.
- `src/p2p_arb_bot/infrastructure/binance_p2p.py`: conserva par solicitado en metadatos.
- `src/p2p_arb_bot/infrastructure/sqlite_repo.py`: historial de rutas agregado exacto.
- `src/p2p_arb_bot/web/app.py`, `db_reader.py`, `templates/dashboard.html`:
  lectura y exposición del panel V1.
- `tests/test_web_endpoints.py`: render de health, rutas, legs e historial V1.

Nuevos:

- `src/p2p_arb_bot/domain/execution.py`: filtros, fills, costes, redes y optimizador.
- `src/p2p_arb_bot/domain/payments.py`: aliases locales de bancos vietnamitas.
- `src/p2p_arb_bot/application/scanner.py`: ciclos, aislamiento y alertas continuas.
- `src/p2p_arb_bot/scanner_config.py`: configuración validada del scanner.
- `src/p2p_arb_bot/infrastructure/exchanges.py`: Binance, OKX, Bybit, BingX mock y resiliencia.
- `src/p2p_arb_bot/infrastructure/scanner_output.py`: snapshot manual y status atómico.
- `src/p2p_arb_bot/infrastructure/telegram_notifier.py`: alertas Telegram opcionales.
- `src/p2p_arb_bot/web/templates/partials/scanner.html`: dashboard por tamaño/ruta.
- `scripts/smoke_endpoints.py`: smoke explícito read-only en ambos lados.
- `tests/test_execution_v1.py`, `test_exchanges_v1.py`, `test_scanner_v1.py`.
- `docs/V1_IMPLEMENTATION.md`: este informe.

## Validación realizada

- `venv/bin/python -m pytest`: **192 passed**, incluidos los 137 tests previos.
  Los tests no hacen llamadas a endpoints de mercado. En este sandbox, asyncio y
  TestClient necesitan permiso de sockets locales para no bloquearse; se ejecutó
  la suite con ese permiso. No se cambió la suite antigua para evitar los tests web.
- `venv/bin/python -m compileall -q src tests scripts`: correcto.
- `git diff --check`: correcto. No hay lint ni type checker configurados;
  `make lint` no tiene implementación y no se introduce tooling nuevo.
- Golden: 26m @ 25.785 + 12m @ 25.786 + 9,3m @ 25.804 =
  **1.834,11622476853 USDT**, precio agrupado **25.788,9872851266 VND/USDT**.
- Smoke live read-only, 2026-10-31: Binance y Bybit devolvieron 10 ads por lado;
  OKX devolvió snapshots no vacíos en BUY y SELL. Se verificó en respuesta real
  que OKX query/book usan lado maker y `paymentTimeoutMinutes`. BingX devuelve
  el error experimental esperado. Estos resultados validan transporte/parseo,
  no certifican que un arbitraje se pueda ejecutar en la práctica.
- Chromium/Playwright: render con fixtures a 1440px y 390px, sin errores JavaScript
  ni overflow horizontal del documento. Capturas locales de fixture en
  `/tmp/p2p-v1-dashboard.png` y `/tmp/p2p-v1-dashboard-mobile.png`; no se commitean.

## Limitaciones pendientes

- BingX no tiene endpoint P2P verificado: feature flag desactivado, contrato interno
  de tests y fallo aislado. No se presenta como integración live operativa.
- Endpoints públicos no oficiales pueden cambiar, bloquear o limitar consultas.
- No se adivina banco a partir de códigos Bybit ni métodos genéricos; filtros
  específicos rechazan métodos sin mapping verificado.
- Las redes/fees/estados se verifican e introducen manualmente. Sin snapshots
  vigentes de ambos extremos, ninguna ruta se anuncia como factible.
- Walk de precio conservador: no redistribuye legs previos para satisfacer un
  mínimo posterior; sin paginación de libros en esta versión.
- Edad desde recepción local; la antigüedad real del anuncio en el exchange puede
  ser mayor. El dashboard muestra timestamp del último barrido si el bot se detiene.
- Historial agregado por ruta/tamaño/red; no es un registro de cada tick individual.
  El gating continuo es independiente de `seen_count` y se reinicia con el proceso.
- Telegram probado como integración opcional en código; no se envió ningún mensaje
  real y el entorno necesita egress adicional a api.telegram.org para usarlo.

## Validación manual y siguiente fase

1. Copiar `.env.example`; revisar que un `.env` previo no conserve `FIAT=VES`.
2. Configurar USDT/VND y 47.300.000 VND; cargar redes/fees/timestamps verificados.
3. Ejecutar tests, smoke y dashboard. Contrastar lados, límites, métodos y legs
   con los libros de los exchanges; no tratar spread headline como ejecutable.
4. Revisar rechazo cuando falta liquidez o caducan quotes/redes; simular una fuente
   caída y comprobar que las otras siguen actualizando.
5. Verificar mínimos de scans y duración antes de alertar; comprobar que un ciclo
   no rentable reinicia continuidad. Telegram requiere configuración propia.

Próxima fase: fixtures sanitizados de esquemas reales, mapping de pagos verificado,
paginación y proveedor dinámico de costes de red. Mantener la ejecución de fondos
fuera de este scanner.
