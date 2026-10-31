"""Tests de los endpoints FastAPI del dashboard, con TestClient.

Complementan ``test_web.py`` (que solo ejercita las funciones puras de soporte):
aquí se monta la app de verdad y se piden las rutas. Sin red — TestClient habla
con la app en proceso — pero sí con disco: SQLite y plantillas reales sobre
``tmp_path``, que es donde viven los fallos que los tests de funciones no ven
(plantilla rota, ruta sin proteger, contexto que no llega al parcial).

Nada de esto arranca el bot: no se toca ``/bot/start``, que lanzaría un
subproceso real.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from starlette.testclient import TestClient

from p2p_arb_bot.domain.models import Opportunity
from p2p_arb_bot.infrastructure.sqlite_repo import SQLiteRepository
from p2p_arb_bot.web import env_store, trade_store
from p2p_arb_bot.web.app import create_app

PASSWORD = "secreto-de-test"


def _seed_opportunity(db_path: str) -> None:
    repo = SQLiteRepository(db_path)
    repo.save(
        Opportunity(
            detected_at=datetime.now(timezone.utc),
            fiat="VES", asset="USDT",
            buy_pay_method="PagoMovil", sell_pay_method="Banesco",
            buy_price=Decimal("36"), sell_price=Decimal("37"),
            spread_pct=Decimal("2.9"), net_pct=Decimal("2.777"),
            max_usdt=Decimal("100"),
            buy_adv_no="b1", sell_adv_no="s1",
            buy_advertiser="vend1", sell_advertiser="comp1",
            buy_url="https://p2p.binance.com/es/trade/buy/USDT?fiat=VES&payment=PagoMovil",
            sell_url="https://p2p.binance.com/es/trade/sell/USDT?fiat=VES&payment=Banesco",
            est_profit_usdt=Decimal("2.77"),
            buy_min_amount=Decimal("100"), buy_max_amount=Decimal("100000"),
            sell_min_amount=Decimal("100"), sell_max_amount=Decimal("100000"),
        )
    )
    repo.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """App aislada en tmp_path.

    ENV_PATH apunta a un .env vacío a propósito: ``create_app`` llama a
    ``load_dotenv``, que sin ruta buscaría el .env REAL del repo y colaría la
    contraseña y las rutas de producción en los tests.
    """
    (tmp_path / "empty.env").write_text("")
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "empty.env"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "opps.db"))
    monkeypatch.setenv("TRADES_DB_PATH", str(tmp_path / "trades.db"))
    monkeypatch.setenv("STATUS_PATH", str(tmp_path / "status.json"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "bot.log"))
    monkeypatch.setenv("BOT_PIDFILE", str(tmp_path / "bot.pid"))
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("DASHBOARD_SECRET", "secreto-de-firma-para-tests")
    return tmp_path


@pytest.fixture
def client(env):
    return TestClient(create_app())


@pytest.fixture
def authed(client):
    r = client.post("/login", data={"password": PASSWORD})
    assert r.status_code == 200
    return client


# --- supervisión pública -----------------------------------------------------

@pytest.mark.parametrize(
    "route",
    ["/", "/partials/status", "/partials/opportunities", "/partials/trade-amount", "/partials/log"],
)
def test_public_routes_need_no_login(client, route):
    assert client.get(route).status_code == 200


def test_dashboard_renders_with_empty_db(client):
    # Sin DB todavía: el dashboard debe pintar, no reventar.
    r = client.get("/")
    assert r.status_code == 200
    assert "Aún no se han registrado oportunidades" in r.text


@pytest.fixture
def chart_clock(monkeypatch):
    from p2p_arb_bot.web import db_reader
    clock = [datetime(2026, 10, 14, 19, 37, tzinfo=timezone.utc)]

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr(db_reader, "datetime", FixedDatetime)
    return clock


def test_grafica_dashboard_ultimas_24_horas(client, chart_clock):
    body = client.get("/").text
    assert 'id="chart-date"' not in body
    assert 'type="date"' not in body
    assert 'hx-trigger="every 30s, refresh"' in body
    assert body.index("Última oportunidad detectada") < body.index("Oportunidades por hora") < body.index("Oportunidades recientes")
    assert "Desde 14/10/2026 02:37:00 hasta 15/10/2026 02:37:00" in body
    body = client.get("/partials/opportunities-chart").text
    assert "Detecciones por hora en las últimas 24 horas" in body
    chart_clock[0] += timedelta(minutes=30)
    body = client.get("/partials/opportunities-chart").text
    assert "Desde 14/10/2026 03:07:00 hasta 15/10/2026 03:07:00" in body


def test_grafica_ventana_movil_publica_y_limpieza(authed, env, chart_clock):
    import sqlite3
    _seed_opportunity(str(env / "opps.db"))
    with sqlite3.connect(env / "opps.db") as conn:
        conn.execute("UPDATE opportunities SET detected_at = '2026-10-14T16:30:00+00:00'")
    # Cliente sin sesión para verificar que la supervisión sigue siendo pública.
    anonymous = TestClient(create_app())
    response = anonymous.get("/partials/opportunities-chart")
    assert response.status_code == 200
    assert "<strong>1</strong>" in response.text
    assert "detecciones registradas" in response.text
    assert "14/10 22:37–14/10 23:37: 1 detecciones" in response.text
    assert response.text.count('<g class="chart-hour') == 24
    assert "Ver conteos por hora" in response.text
    # Los parámetros del antiguo selector ya no cambian la ventana.
    assert anonymous.get("/partials/opportunities-chart?date=2026-10-12").text == response.text
    chart_clock[0] += timedelta(hours=22)
    assert "Sin detecciones registradas" in anonymous.get("/partials/opportunities-chart").text
    assert "htmx.trigger('#opportunities-chart','refresh')" in authed.get("/").text
    assert authed.post("/opportunities/clear").status_code == 200
    chart_clock[0] -= timedelta(hours=22)
    assert "Sin detecciones registradas" in anonymous.get("/partials/opportunities-chart").text


def test_grafica_base_ausente_y_error_sqlite(client, env):
    response = client.get("/partials/opportunities-chart")
    assert response.status_code == 200
    assert "Sin detecciones registradas en las últimas 24 horas" in response.text
    assert not (env / "opps.db").exists()
    (env / "opps.db").write_text("base dañada")
    response = client.get("/partials/opportunities-chart")
    assert response.status_code == 200
    assert "No se pudieron cargar los datos" in response.text
    assert "Sin detecciones registradas" not in response.text


@pytest.mark.parametrize("route", ["/", "/partials/status"])
def test_resumen_publico_muestra_configuracion_con_bot_detenido(client, monkeypatch, route):
    monkeypatch.setenv("ASSETS", "USDT,BTC")
    monkeypatch.setenv("FIAT", "VES")
    monkeypatch.setenv("PAY_METHODS", "Banesco,PagoMovil")
    monkeypatch.setenv("MAX_FIAT", "12345.67")
    body = client.get(route).text
    assert "Configuración actual" in body
    assert "USDT/VES</span>" in body and "BTC/VES</span>" in body
    assert "Banesco</span>" in body and "PagoMovil</span>" in body
    assert '12345.67 <span class="config-currency">VES</span>' in body
    assert "Se evalúa completo para cada oportunidad." in body
    assert "Editar configuración" not in body


@pytest.mark.parametrize("route", ["/", "/partials/status"])
def test_resumen_sin_filtro_de_bancos_y_enlace_privado(authed, monkeypatch, route):
    monkeypatch.setenv("PAY_METHODS", "")
    body = authed.get(route).text
    assert "Todos los métodos de pago" in body
    assert 'href="/config">Editar configuración</a>' in body


def test_dashboard_explica_funcion_del_bot(client):
    body = client.get("/").text
    assert "Monitorea precios de compra y venta en Binance P2P" in body
    assert "No ejecuta operaciones." in body


# --- rutas protegidas --------------------------------------------------------

PROTECTED_GET = ["/partials/trades", "/trades/new?opp_id=1", "/trades/fail?opp_id=1", "/config"]


@pytest.mark.parametrize("route", PROTECTED_GET)
def test_protected_get_returns_401_to_htmx(client, route):
    # A HTMX se le responde 401 para que no swapee un HTML de login dentro del panel.
    assert client.get(route, headers={"HX-Request": "true"}).status_code == 401


@pytest.mark.parametrize("route", PROTECTED_GET)
def test_protected_get_redirects_browser_to_login(client, route):
    r = client.get(route, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_post_trades_requires_login(client):
    r = client.post(
        "/trades",
        data={"status": "completed", "opportunity_id": 1},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 401


def test_trades_are_not_leaked_to_anonymous_dashboard(client, env):
    """El dashboard es público: el registro de operaciones NO puede asomar en él."""
    trade_store.save_trade(
        str(env / "trades.db"),
        {"status": "completed", "notes": "nota-privada", "buy_advertiser": "vend-privado"},
    )
    body = client.get("/").text
    assert "Mis operaciones" not in body
    assert "nota-privada" not in body
    assert "vend-privado" not in body


# --- configuración -----------------------------------------------------------

@pytest.fixture
def sin_red(monkeypatch):
    """Evita que /config sondee Binance de verdad al descubrir métodos de pago."""
    async def _vacio(asset, fiat, **_kw):
        return []

    monkeypatch.setattr(env_store, "discover_methods", _vacio)


@pytest.fixture
def env_limpio(monkeypatch):
    """Aísla las claves que write_config vuelca en os.environ.

    ``write_config`` hace ``os.environ.update`` a propósito (el bot hereda el
    entorno del dashboard), así que sin esto un test filtraría su config a los
    siguientes. monkeypatch las restaura al terminar.
    """
    for key in ("ASSETS", "MAX_FIAT", "THRESHOLD_PCT", "PAY_METHODS", "POLL_INTERVAL_S"):
        monkeypatch.setenv(key, "")


def test_config_form_lista_las_monedas(authed, sin_red):
    body = authed.get("/config").text
    assert 'name="assets"' in body
    assert "BTC" in body
    assert "Monedas a monitorear" in body


def test_config_guarda_varias_monedas(authed, env, sin_red, env_limpio):
    before = authed.get("/partials/status").text
    assert "80000" not in before
    r = authed.post(
        "/config",
        data={
            "threshold_pct": "1.5",
            "max_fiat": "80000",
            "poll_interval_s": "30",
            "assets": ["USDT", "BTC"],
        },
    )
    assert r.status_code == 200
    assert "Configuración guardada" in r.text
    lineas = (env / "empty.env").read_text(encoding="utf-8").splitlines()
    assert "ASSETS=USDT,BTC" in lineas
    assert "MAX_FIAT=80000" in lineas
    body = authed.get("/partials/status").text
    assert "80000" in body
    assert "USDT/" in body and "BTC/" in body


def test_config_sin_monedas_muestra_error(authed, env, sin_red, env_limpio):
    r = authed.post(
        "/config",
        data={"threshold_pct": "1.5", "max_fiat": "80000", "poll_interval_s": "30"},
    )
    assert r.status_code == 200
    assert "No se pudo guardar" in r.text
    assert "al menos una moneda" in r.text
    # Y no escribió nada en el .env.
    assert "ASSETS=" not in (env / "empty.env").read_text(encoding="utf-8")


# --- login -------------------------------------------------------------------

def test_login_with_wrong_password_is_rejected(client):
    r = client.post("/login", data={"password": "incorrecta"})
    assert r.status_code == 401
    assert client.get("/partials/trades", headers={"HX-Request": "true"}).status_code == 401


def test_login_grants_access(authed):
    assert authed.get("/partials/trades").status_code == 200


# --- oportunidades: contrato con notify.js -----------------------------------

def test_opportunities_expose_id_for_notifications(client, env):
    """notify.js compara data-opp-id con el último visto: sin id no hay aviso."""
    _seed_opportunity(str(env / "opps.db"))
    body = client.get("/partials/opportunities").text
    assert 'data-opp-id="1"' in body
    assert "data-buy-url=" in body
    assert 'data-pair="USDT/VES"' in body


def test_opportunities_partial_keeps_buttons_after_refresh(authed, env):
    """El parcial se repinta cada 8 s: si no recibe `authed`, los botones se caen."""
    _seed_opportunity(str(env / "opps.db"))
    body = authed.get("/partials/opportunities").text
    assert "/trades/new?opp_id=1" in body
    assert "/trades/fail?opp_id=1" in body


def test_opportunities_partial_hides_buttons_when_anonymous(client, env):
    _seed_opportunity(str(env / "opps.db"))
    body = client.get("/partials/opportunities").text
    assert "/trades/new" not in body


# --- formularios de registro -------------------------------------------------

def test_trade_form_carries_estimate_snapshot(authed, env):
    """El snapshot viaja en el form; el POST no relee la DB (puede estar bloqueada)."""
    _seed_opportunity(str(env / "opps.db"))
    body = authed.get("/trades/new?opp_id=1").text
    assert 'name="est_net_pct" value="2.777"' in body
    assert 'name="asset" value="USDT"' in body
    assert 'name="est_profit_usdt" value="2.77"' in body


def test_trade_form_survives_missing_opportunity(authed):
    """Si el usuario pulsó Limpiar entre medias, el form sale vacío, no revienta."""
    r = authed.get("/trades/new?opp_id=999")
    assert r.status_code == 200
    assert 'name="est_net_pct" value=""' in r.text


def test_fail_form_lists_reasons(authed, env):
    _seed_opportunity(str(env / "opps.db"))
    body = authed.get("/trades/fail?opp_id=1").text
    for key in trade_store.FAILURE_REASONS:
        assert f'value="{key}"' in body


# --- guardar y renderizar ----------------------------------------------------

def _post_completed(client, **over):
    data = {
        "status": "completed", "opportunity_id": 1,
        "detected_at": "2026-07-15T10:00:00+00:00", "asset": "USDT", "fiat": "VES",
        "est_net_pct": "2.777", "est_profit_usdt": "2.77",
        "real_buy_price": "36", "real_sell_price": "37", "real_usdt": "100",
        "real_fiat_buy": "3600", "real_fiat_sell": "3650",
        "buy_advertiser": "vend1", "sell_advertiser": "comp1",
    }
    data.update(over)
    return client.post("/trades", data=data)


def test_post_completed_saves_and_renders_comparison(authed):
    r = _post_completed(authed)
    assert r.status_code == 200
    # Real 1.389% frente al 2.777% estimado: el hueco es lo que se quiere ver.
    assert "1.389%" in r.text
    assert "2.777%" in r.text
    assert "-1.388" in r.text


def test_post_failed_renders_without_crashing(authed):
    """Regresión: una operación fallida no tiene PnL.

    Las claves de PnL deben existir a None; si faltan, Jinja las ve como Undefined,
    ``Undefined is not none`` da True, y la plantilla revienta con un 500 al
    formatear el delta. La suite de funciones puras no detectaba esto.
    """
    r = authed.post(
        "/trades",
        data={
            "status": "failed", "opportunity_id": 1,
            "detected_at": "2026-07-15T10:00:00+00:00", "asset": "USDT", "fiat": "VES",
            "est_net_pct": "4.0", "failure_reason": "too_late", "notes": "el anuncio voló",
        },
    )
    assert r.status_code == 200
    assert "no pude" in r.text
    assert "No llegué a tiempo / el anuncio expiró" in r.text


def test_mixed_history_reports_hit_rate(authed):
    _post_completed(authed)
    authed.post("/trades", data={"status": "failed", "opportunity_id": 1, "failure_reason": "no_funds"})
    body = authed.get("/partials/trades").text
    assert "(50%)" in body  # 1 cerrada de 2 registradas


def test_unknown_status_is_not_trusted(authed):
    """El status llega del cliente: cualquier cosa que no sea 'failed' es 'completed'."""
    r = _post_completed(authed, status="cualquier-cosa")
    assert r.status_code == 200
    assert "cerrada" in r.text


def test_profit_btc_conserva_precision_y_unidad(client, env):
    import sqlite3

    _seed_opportunity(str(env / "opps.db"))
    with sqlite3.connect(env / "opps.db") as conn:
        conn.execute("UPDATE opportunities SET asset = 'BTC', est_profit_usdt = '0.000002'")
    response = client.get("/partials/opportunities")
    assert response.status_code == 200
    assert "~0.000002 BTC" in response.text
    assert "— USD" in response.text
    assert "/ — USD" in client.get("/partials/status").text


@pytest.mark.parametrize("con_sesion", [False, True])
def test_dashboard_muestra_ganancias_usd(client, env, con_sesion):
    _seed_opportunity(str(env / "opps.db"))
    if con_sesion:
        client.post("/login", data={"password": PASSWORD})
    for path in ("/", "/partials/opportunities"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'data-label="Ganancia est. (USD)"' in response.text
        assert "~2.77 USD" in response.text
        assert response.text.index('data-label="Ganancia est."') < response.text.index('data-label="Ganancia est. (USD)"')
    for path in ("/", "/partials/status"):
        response = client.get(path)
        assert response.status_code == 200
        assert "~99.72 VES / ~2.77 USD" in response.text


def test_ganancia_usd_vacia(client):
    response = client.get("/partials/status")
    assert response.status_code == 200
    assert "/ ~0.00 USD" in response.text


@pytest.mark.parametrize("falla_primera", [False, True])
def test_config_combina_metodos_de_todas_las_monedas(authed, monkeypatch, falla_primera):
    monkeypatch.setenv("ASSETS", "USDT,BTC")
    calls = []

    async def discover(asset, fiat):
        calls.append(asset)
        if asset == "USDT":
            if falla_primera:
                raise TimeoutError("sin respuesta")
            return [("PagoMovil", "PagoMovil"), ("Banesco", "Banesco")]
        return [("Banesco", "Banesco"), ("Mercantil", "Mercantil")]

    monkeypatch.setattr(env_store, "discover_methods", discover)
    response = authed.get("/config")
    assert response.status_code == 200
    assert calls == ["USDT", "BTC"]
    assert response.text.count('value="Banesco"') == 1
    assert 'value="Mercantil"' in response.text
    assert ('value="PagoMovil"' in response.text) == (not falla_primera)


def test_panel_registro_solo_con_sesion_y_fuera_del_refresco(client, authed):
    body = authed.get('/').text
    assert '<dialog id="trade-dialog"' in body
    assert 'id="trade-form"' in body
    assert 'aria-labelledby="dialog-title"' in body
    assert '<details class="card log-panel">' in body
    authed.post('/logout')
    public = client.get('/').text
    assert '<dialog' not in public
    assert 'id="trade-form"' not in public


def test_formularios_del_panel_conservan_snapshot_y_control_de_envio(authed, env):
    _seed_opportunity(str(env / 'opps.db'))
    for route in ('/trades/new?opp_id=1', '/trades/fail?opp_id=1'):
        response = authed.get(route)
        assert response.status_code == 200
        assert 'name="est_profit_usdt" value="2.77"' in response.text
        assert 'hx-disabled-elt="find button[type=submit]"' in response.text
        assert 'data-close-dialog' in response.text


def test_oportunidad_una_sola_fila_para_avisos_y_etiquetas_moviles(client, env):
    _seed_opportunity(str(env / 'opps.db'))
    body = client.get('/partials/opportunities').text
    assert body.count('data-opp-id="1"') == 1
    assert 'data-label="Par"' in body
    assert 'data-label="Ganancia est."' in body


def test_historial_muestra_ganancias_btc_sin_redondear_a_cero(authed):
    response = _post_completed(
        authed, asset='BTC', real_usdt='0.0001',
        real_fiat_buy='1000', real_fiat_sell='1020',
    )
    assert response.status_code == 200
    assert '~0.000002 BTC' in response.text
    assert 'Ganancia realizada' in response.text


def test_scanner_v1_routes_health_and_selected_legs(client, env):
    import asyncio
    import json
    from p2p_arb_bot.infrastructure.scanner_output import ScannerStatus
    from p2p_arb_bot.domain.models import WatchTarget
    from tests.test_execution_v1 import optimize, NOW
    route = optimize()[0]
    repo = SQLiteRepository(str(env / "opps.db"))
    repo.save_route(route, NOW, {"Binance": {"status": "ok"}})
    repo.close()
    status = ScannerStatus(str(env / "status.json"))
    asyncio.run(status.publish(WatchTarget("USDT", "VND", (), Decimal("47300000"), Decimal("0.1")),
                               [route], {"Binance": {"status": "ok", "ads": 2},
                                         "BingX": {"status": "disabled", "reason": "experimental"}}))
    for path in ("/", "/partials/scanner"):
        response = client.get(path)
        assert response.status_code == 200
        body = response.text
        assert "Binance → OKX" in body and "47300000" in body
        assert "PUBLIC_WEB_ENDPOINT" in body and "TRC20" in body
        assert "BingX: disabled" in body and "contrapartes" in body
        assert "Historial reciente V1" in body


def test_scanner_partial_without_database_or_status(client):
    response = client.get("/partials/scanner")
    assert response.status_code == 200 and "Esperando el primer barrido" in response.text


def test_directional_network_details_and_safe_collector_health(client, env):
    import asyncio
    from dataclasses import replace
    from p2p_arb_bot.infrastructure.scanner_output import ScannerStatus
    from p2p_arb_bot.domain.models import WatchTarget
    from tests.test_execution_v1 import optimize, networks, NOW
    snapshots=tuple(replace(n, deposit_enabled=False) if n.exchange=="OKX" else n for n in networks())
    routes=optimize(networks=snapshots)
    health={"network_costs":{"status":"empty", "exchanges":{"OKX":{"status":"error", "code":"access_denied", "reason":"Acceso denegado"}}}}
    asyncio.run(ScannerStatus(str(env/"status.json")).publish(
        WatchTarget("USDT","VND",(),Decimal("47300000"),Decimal("0.1")),routes,health))
    body=client.get("/partials/scanner").text
    assert "Network API OKX: error" in body and "access_denied" in body
    assert "depósito deshabilitado en destino" in body and "TRC20" in body
    assert "withdraw=True" in body and "deposit=False" in body


def test_account_snapshot_is_private_fresh_and_not_cached(client, authed, env, monkeypatch):
    import json
    path=env/'accounts.json'
    monkeypatch.setenv('ACCOUNT_STATUS_PATH',str(path))
    payload={'last_update':datetime.now(timezone.utc).isoformat(),'checkpoint':'BLOCKED',
             'health':{},'balances':[{'exchange':'BingX','scope':'spot','asset':'UNI','available':'PRIVATE-42','locked':'1','timestamp':'now'}],
             'orders':[],'order_coverage':'UNI-USDT','market':None,'fees':None,
             'uni_review':{'ready':False,'reason':'fee unknown'}}
    path.write_text(json.dumps(payload))
    response=authed.get('/partials/accounts')
    assert response.status_code==200 and 'PRIVATE-42' in response.text
    assert response.headers['cache-control']=='no-store'
    authed.post('/logout')
    response=client.get('/partials/accounts',headers={'HX-Request':'true'})
    assert response.status_code==401 and 'PRIVATE-42' not in response.text
    assert 'PRIVATE-42' not in client.get('/').text
    authed.post('/login',data={'password':PASSWORD})
    payload['last_update']=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
    path.write_text(json.dumps(payload))
    response=authed.get('/partials/accounts')
    assert 'PRIVATE-42' not in response.text and 'Sin snapshot privado vigente' in response.text
