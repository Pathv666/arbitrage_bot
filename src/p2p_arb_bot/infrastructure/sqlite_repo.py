"""Adaptador ``OpportunityRepository`` sobre SQLite (stdlib)."""

from __future__ import annotations

import logging
import json
import sqlite3
from datetime import datetime, timedelta

from ..domain.models import Opportunity
from ..domain.execution import Route
from .scanner_output import route_payload, route_key

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at     TEXT    NOT NULL,
    fiat            TEXT    NOT NULL,
    asset           TEXT    NOT NULL,
    buy_pay_method  TEXT    NOT NULL,
    sell_pay_method TEXT    NOT NULL,
    buy_price       TEXT    NOT NULL,
    sell_price      TEXT    NOT NULL,
    spread_pct      REAL    NOT NULL,
    net_pct         REAL    NOT NULL,
    max_usdt        TEXT    NOT NULL,
    buy_adv_no      TEXT    NOT NULL,
    sell_adv_no     TEXT    NOT NULL,
    buy_advertiser  TEXT    NOT NULL,
    sell_advertiser TEXT    NOT NULL,
    buy_url         TEXT    NOT NULL,
    sell_url        TEXT    NOT NULL,
    est_profit_usdt TEXT    NOT NULL DEFAULT '0',
    buy_min_amount  TEXT    NOT NULL DEFAULT '0',
    buy_max_amount  TEXT    NOT NULL DEFAULT '0',
    sell_min_amount TEXT    NOT NULL DEFAULT '0',
    sell_max_amount TEXT    NOT NULL DEFAULT '0'
);
CREATE INDEX IF NOT EXISTS idx_opportunities_detected_at
    ON opportunities (detected_at);
"""


class SQLiteRepository:
    """Persiste oportunidades en SQLite y detecta duplicados recientes."""

    def __init__(self, db_path: str = "opportunities.db") -> None:
        # check_same_thread=False: el bucle asyncio puede tocar la conexión desde
        # callbacks distintos; el acceso es secuencial, así que es seguro aquí.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.execute("""CREATE TABLE IF NOT EXISTS route_history (
            route_key TEXT PRIMARY KEY, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            seen_count INTEGER NOT NULL, buy_exchange TEXT, sell_exchange TEXT,
            requested_size TEXT, buy_price TEXT, sell_price TEXT, gross_edge TEXT,
            net_edge TEXT, status TEXT, payload TEXT NOT NULL, datasource_health TEXT NOT NULL
        )""")
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Añade columnas nuevas a BDs creadas con un esquema anterior."""
        existing = {
            row[1] for row in self._conn.execute("PRAGMA table_info(opportunities)")
        }
        for column in (
            "est_profit_usdt",
            "buy_min_amount",
            "buy_max_amount",
            "sell_min_amount",
            "sell_max_amount",
        ):
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE opportunities "
                    f"ADD COLUMN {column} TEXT NOT NULL DEFAULT '0'"
                )

    def save(self, opp: Opportunity) -> None:
        self._conn.execute(
            """
            INSERT INTO opportunities (
                detected_at, fiat, asset, buy_pay_method, sell_pay_method,
                buy_price, sell_price, spread_pct, net_pct, max_usdt,
                buy_adv_no, sell_adv_no, buy_advertiser, sell_advertiser,
                buy_url, sell_url, est_profit_usdt,
                buy_min_amount, buy_max_amount, sell_min_amount, sell_max_amount
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                opp.detected_at.isoformat(),
                opp.fiat,
                opp.asset,
                opp.buy_pay_method,
                opp.sell_pay_method,
                str(opp.buy_price),
                str(opp.sell_price),
                float(opp.spread_pct),
                float(opp.net_pct),
                str(opp.max_usdt),
                opp.buy_adv_no,
                opp.sell_adv_no,
                opp.buy_advertiser,
                opp.sell_advertiser,
                opp.buy_url,
                opp.sell_url,
                str(opp.est_profit_usdt),
                str(opp.buy_min_amount),
                str(opp.buy_max_amount),
                str(opp.sell_min_amount),
                str(opp.sell_max_amount),
            ),
        )
        self._conn.commit()

    def recent_duplicate(self, opp: Opportunity, window_s: int) -> bool:
        """¿Misma combinación advNo + precios registrada en la ventana reciente?"""
        cutoff = (opp.detected_at - timedelta(seconds=window_s)).isoformat()
        cur = self._conn.execute(
            """
            SELECT 1 FROM opportunities
            WHERE buy_adv_no = ? AND sell_adv_no = ?
              AND buy_price = ? AND sell_price = ?
              AND detected_at >= ?
            LIMIT 1
            """,
            (
                opp.buy_adv_no,
                opp.sell_adv_no,
                str(opp.buy_price),
                str(opp.sell_price),
                cutoff,
            ),
        )
        return cur.fetchone() is not None

    def save_route(self, route: Route, now: datetime, health: dict) -> None:
        self._conn.execute("""
            INSERT INTO route_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(route_key) DO UPDATE SET
              last_seen=excluded.last_seen, seen_count=route_history.seen_count+1,
              buy_price=excluded.buy_price, sell_price=excluded.sell_price,
              gross_edge=excluded.gross_edge, net_edge=excluded.net_edge,
              status=excluded.status, payload=excluded.payload,
              datasource_health=excluded.datasource_health
        """, (route_key(route), now.isoformat(), now.isoformat(), 1,
              route.buy_exchange, route.sell_exchange, str(route.requested_size),
              str(route.buy.effective_price), str(route.sell.effective_price),
              str(route.gross_spread_pct), str(route.net_edge_pct),
              "feasible" if route.feasible else "rejected",
              json.dumps(route_payload(route)), json.dumps(health)))
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error as exc:  # pragma: no cover - cierre best-effort
            logger.debug("Error cerrando SQLite: %s", exc)
