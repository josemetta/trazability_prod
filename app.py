"""Metta · Trazabilidad logística China → Lima.

MVP Streamlit para el control de solicitudes/órdenes de compra (BOM PCB),
pagos con SLA y seguimiento de acopio / producción / tránsito desde China
hasta el taller en Lima (proceso de 10 etapas).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="Metta · Trazabilidad de producción",
    page_icon=":material/local_shipping:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Constantes de dominio
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "trazabilidad.db"
UPLOAD_DIR = BASE_DIR / "uploads"
SCHEMA_VERSION = 2

DATE_FMT = "%Y-%m-%d %H:%M:%S"

ROL_SOLICITUD = "usuario_01"  # José
ROL_LOGISTICA = "usuario_02"  # Adrián
ROL_FINANZAS = "usuario_03"  # Julio
ROL_TALLER = "usuario_04"  # Lucho
ROLES_VALIDOS = {ROL_SOLICITUD, ROL_LOGISTICA, ROL_FINANZAS, ROL_TALLER}

ROLES = {
    ROL_SOLICITUD: {
        "label": "Usuario 01 · José · Solicitud de compra",
        "corto": "José · Solicitud",
        "icon": ":material/assignment_add:",
    },
    ROL_LOGISTICA: {
        "label": "Usuario 02 · Adrián · Órdenes / logística China",
        "corto": "Adrián · Logística",
        "icon": ":material/shopping_cart:",
    },
    ROL_FINANZAS: {
        "label": "Usuario 03 · Julio · Finanzas / pagos",
        "corto": "Julio · Finanzas",
        "icon": ":material/payments:",
    },
    ROL_TALLER: {
        "label": "Usuario 04 · Lucho · Recepción taller Lima",
        "corto": "Lucho · Taller",
        "icon": ":material/factory:",
    },
}

ETAPA_SOLICITUD = "solicitud_compra"
ETAPA_ORDEN_BOM = "orden_bom"
ETAPA_PAGO_INVOICE_P01 = "pago_invoice_p01"
ETAPA_ACOPIO_P01 = "acopio_p01"
ETAPA_TRANSITO_P2 = "transito_p2"
ETAPA_PAGO_INVOICE_P02 = "pago_invoice_p02"
ETAPA_PRODUCCION_P02 = "produccion_p02"
ETAPA_ENVIO_PERU = "envio_peru"
ETAPA_ADUANAS = "aduanas_aranceles"
ETAPA_TALLER = "taller_lima"

TOTAL_ETAPAS = 10

ETAPAS = {
    ETAPA_SOLICITUD: {
        "orden": 1,
        "nombre": "Solicitud de compra",
        "detalle": "Usuario 01 genera la solicitud y adjunta la Orden General del ERP",
        "region": "lima",
    },
    ETAPA_ORDEN_BOM: {
        "orden": 2,
        "nombre": "Orden BOM PCB",
        "detalle": "Usuario 02 sube la Purchase Order o enlace de Slack · SLA 4 días",
        "region": "lima",
    },
    ETAPA_PAGO_INVOICE_P01: {
        "orden": 3,
        "nombre": "Pago invoice P01 (QZ)",
        "detalle": "Usuario 03 marca invoice pagado y adjunta comprobante · SLA 2 días",
        "region": "china",
    },
    ETAPA_ACOPIO_P01: {
        "orden": 4,
        "nombre": "Acopio P01 (QZ)",
        "detalle": "Usuario 02 confirma pedido registrado / en acopio · SLA 2 días",
        "region": "china",
    },
    ETAPA_TRANSITO_P2: {
        "orden": 5,
        "nombre": "Tránsito a P02 (JLC)",
        "detalle": "Conteo automático de 4 días hacia Proveedor 02",
        "region": "china",
    },
    ETAPA_PAGO_INVOICE_P02: {
        "orden": 6,
        "nombre": "Pago invoice P02 (JLC)",
        "detalle": "Usuario 03 marca invoice pagado y adjunta comprobante · SLA 1 día",
        "region": "china",
    },
    ETAPA_PRODUCCION_P02: {
        "orden": 7,
        "nombre": "Producción P02 (JLC)",
        "detalle": "Usuario 02 marca en producción · SLA 2 días · fase 21 días",
        "region": "china",
    },
    ETAPA_ENVIO_PERU: {
        "orden": 8,
        "nombre": "Envío a Perú",
        "detalle": "Usuario 02 marca tránsito a Perú · SLA 21 días · tránsito 7 días",
        "region": "transito",
    },
    ETAPA_ADUANAS: {
        "orden": 9,
        "nombre": "Aduanas / aranceles",
        "detalle": "Usuario 03 paga aranceles + DHL y adjunta comprobante · SLA 7 días",
        "region": "aduanas",
    },
    ETAPA_TALLER: {
        "orden": 10,
        "nombre": "Recepción taller Lima",
        "detalle": "Usuario 04 confirma recepción en taller · SLA 1 día",
        "region": "taller",
    },
}

DURACION_TRANSITO_P2 = 4
DURACION_PRODUCCION_P02 = 21
DURACION_ENVIO = 7

SLA_ORDEN_BOM_DIAS = 4
SLA_INVOICE_P01_DIAS = 2
SLA_ACOPIO_DIAS = 2
SLA_INVOICE_P02_DIAS = 1
SLA_PRODUCCION_DIAS = 2
SLA_ENVIO_DIAS = 21
SLA_ARANCELES_DIAS = 7
SLA_TALLER_DIAS = 1

PROVEEDOR_QZ = "Shenzhen QZ Industrial Co., Ltd (QZ)"
PROVEEDOR_JLC = "JiaLiChuang (HongKong) Co., Limited (JLC)"

PROVEEDORES_01 = [
    PROVEEDOR_QZ,
    "Shenzhen Electronics Co.",
    "Foxconn Shenzhen",
    "Huaqin Technology",
]
PROVEEDORES_02 = [
    PROVEEDOR_JLC,
    "Dongguan Assembly Ltd.",
    "Suzhou Integration Works",
    "Ningbo Board Assembly",
]

COLORES_FASE = {
    "Solicitud de compra": "#64748B",
    "Orden BOM PCB": "#0F766E",
    "Pago invoice P01": "#B45309",
    "Acopio P01 (QZ)": "#0F6E6B",
    "Tránsito a P02": "#0E7490",
    "Pago invoice P02": "#C2410C",
    "Producción P02 (JLC)": "#1D4E89",
    "Envío a Perú": "#C45C26",
    "Aduanas / aranceles": "#7C3AED",
    "Recepción taller": "#15803D",
}


# ---------------------------------------------------------------------------
# Utilidades de tiempo
# ---------------------------------------------------------------------------


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, DATE_FMT)


def fmt_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime(DATE_FMT)


def now_operativo() -> datetime:
    offset = int(st.session_state.get("sim_days", 0))
    return datetime.now().replace(microsecond=0) + timedelta(days=offset)


def dias_entre(inicio: datetime | None, fin: datetime) -> float | None:
    if inicio is None:
        return None
    return (fin - inicio).total_seconds() / 86400.0


def asegurar_span(inicio: datetime, fin: datetime) -> datetime:
    if fin <= inicio:
        return inicio + timedelta(hours=8)
    return fin


# ---------------------------------------------------------------------------
# Persistencia SQLite
# ---------------------------------------------------------------------------


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if row is None:
        return 0
    ver = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(ver["value"]) if ver else 0


def init_db() -> None:
    """Crea el esquema v2 (10 etapas) y puebla órdenes de demostración."""
    with get_conn() as conn:
        version = _current_schema_version(conn)
        if version < SCHEMA_VERSION:
            conn.executescript(
                """
                DROP TABLE IF EXISTS events;
                DROP TABLE IF EXISTS orders;
                DROP TABLE IF EXISTS meta;
                """
            )

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bom_code TEXT UNIQUE NOT NULL,
                descripcion TEXT NOT NULL,
                sku TEXT,
                cantidad INTEGER NOT NULL,
                proveedor_01 TEXT NOT NULL,
                proveedor_02 TEXT NOT NULL,
                orden_general_nombre TEXT,
                po_nombre TEXT,
                slack_link TEXT,
                invoice_p01_nombre TEXT,
                comprobante_p01_nombre TEXT,
                invoice_p02_nombre TEXT,
                comprobante_p02_nombre TEXT,
                comprobante_aranceles_nombre TEXT,
                fecha_inicio TEXT NOT NULL,
                etapa TEXT NOT NULL,
                created_at TEXT NOT NULL,
                orden_bom_at TEXT,
                invoice_p01_paid_at TEXT,
                acopio_at TEXT,
                transito_p2_at TEXT,
                llego_p2_at TEXT,
                invoice_p02_paid_at TEXT,
                produccion_at TEXT,
                envio_at TEXT,
                aduanas_at TEXT,
                aranceles_paid_at TEXT,
                taller_at TEXT,
                created_by TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                actor TEXT NOT NULL,
                accion TEXT NOT NULL,
                detalle TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            );
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        existing = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        if existing == 0:
            _seed_orders(conn)


def reset_demo() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM orders")
        _seed_orders(conn)
    st.session_state.sim_days = 0


def _log_event(
    conn: sqlite3.Connection,
    order_id: int,
    actor: str,
    accion: str,
    detalle: str,
    cuando: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO events (order_id, actor, accion, detalle, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (order_id, actor, accion, detalle, fmt_dt(cuando)),
    )


def _insert_order(conn: sqlite3.Connection, payload: dict, eventos: list[tuple]) -> None:
    columns = ", ".join(payload.keys())
    placeholders = ", ".join("?" for _ in payload)
    cur = conn.execute(
        f"INSERT INTO orders ({columns}) VALUES ({placeholders})",
        list(payload.values()),
    )
    order_id = cur.lastrowid
    for actor, accion, detalle, cuando in eventos:
        _log_event(conn, order_id, actor, accion, detalle, cuando)


def _seed_orders(conn: sqlite3.Connection) -> None:
    t0 = datetime.now().replace(microsecond=0)

    # BOM-2026-001 · espera generación de orden BOM (SLA 4 días · día 1)
    inicio_001 = t0 - timedelta(days=1)
    _insert_order(
        conn,
        {
            "bom_code": "BOM-2026-001",
            "descripcion": "Mainboards control IoT v3 — lote piloto",
            "sku": "MB-IOT-V3",
            "cantidad": 250,
            "proveedor_01": PROVEEDOR_QZ,
            "proveedor_02": PROVEEDOR_JLC,
            "orden_general_nombre": "OG-ERP-8841.pdf",
            "fecha_inicio": fmt_dt(inicio_001),
            "etapa": ETAPA_ORDEN_BOM,
            "created_at": fmt_dt(inicio_001),
            "created_by": ROL_SOLICITUD,
        },
        [
            (
                ROL_SOLICITUD,
                "Solicitud creada",
                "Orden General OG-ERP-8841.pdf adjunta",
                inicio_001,
            )
        ],
    )

    # BOM-2026-002 · producción JLC (día 5 de 21)
    inicio_002 = t0 - timedelta(days=18)
    orden_002 = inicio_002 + timedelta(days=2)
    pago_p01_002 = orden_002 + timedelta(days=1)
    acopio_002 = pago_p01_002 + timedelta(days=1)
    transito_002 = acopio_002
    llego_002 = transito_002 + timedelta(days=DURACION_TRANSITO_P2)
    pago_p02_002 = llego_002 + timedelta(hours=6)
    prod_002 = pago_p02_002 + timedelta(hours=4)
    _insert_order(
        conn,
        {
            "bom_code": "BOM-2026-002",
            "descripcion": "Mainboards gateway industrial + carcasa",
            "sku": "MB-GW-IND",
            "cantidad": 400,
            "proveedor_01": PROVEEDOR_QZ,
            "proveedor_02": PROVEEDOR_JLC,
            "orden_general_nombre": "OG-ERP-2209.pdf",
            "po_nombre": "PO-QZ-2209.pdf",
            "slack_link": "https://slack.com/archives/C01COMPRAS/p1725000002",
            "invoice_p01_nombre": "INV-QZ-2209.pdf",
            "comprobante_p01_nombre": "PAY-QZ-2209.pdf",
            "invoice_p02_nombre": "INV-JLC-2209.pdf",
            "comprobante_p02_nombre": "PAY-JLC-2209.pdf",
            "fecha_inicio": fmt_dt(inicio_002),
            "etapa": ETAPA_PRODUCCION_P02,
            "created_at": fmt_dt(inicio_002),
            "orden_bom_at": fmt_dt(orden_002),
            "invoice_p01_paid_at": fmt_dt(pago_p01_002),
            "acopio_at": fmt_dt(acopio_002),
            "transito_p2_at": fmt_dt(transito_002),
            "llego_p2_at": fmt_dt(llego_002),
            "invoice_p02_paid_at": fmt_dt(pago_p02_002),
            "produccion_at": fmt_dt(prod_002),
            "created_by": ROL_SOLICITUD,
        },
        [
            (ROL_SOLICITUD, "Solicitud creada", "Orden General ERP", inicio_002),
            (ROL_LOGISTICA, "Orden BOM generada", "PO-QZ-2209.pdf", orden_002),
            (ROL_FINANZAS, "Invoice P01 pagado", "Comprobante QZ", pago_p01_002),
            (ROL_LOGISTICA, "Acopio P01", "Pedido registrado en QZ", acopio_002),
            ("sistema", "Llegada a P02", "Tránsito 4 días completado", llego_002),
            (ROL_FINANZAS, "Invoice P02 pagado", "Comprobante JLC", pago_p02_002),
            (ROL_LOGISTICA, "En producción P02", "Fase de 21 días iniciada", prod_002),
        ],
    )

    # BOM-2026-003 · en tránsito a Perú (día 3 de 7)
    inicio_003 = t0 - timedelta(days=40)
    orden_003 = inicio_003 + timedelta(days=1)
    pago_p01_003 = orden_003 + timedelta(days=1)
    acopio_003 = pago_p01_003 + timedelta(days=1)
    transito_003 = acopio_003
    llego_003 = transito_003 + timedelta(days=DURACION_TRANSITO_P2)
    pago_p02_003 = llego_003 + timedelta(hours=4)
    prod_003 = pago_p02_003 + timedelta(hours=3)
    envio_003 = prod_003 + timedelta(days=DURACION_PRODUCCION_P02)
    _insert_order(
        conn,
        {
            "bom_code": "BOM-2026-003",
            "descripcion": "Lote mainboards residenciales 2026-Q3",
            "sku": "MB-RES-Q3",
            "cantidad": 800,
            "proveedor_01": PROVEEDOR_QZ,
            "proveedor_02": PROVEEDOR_JLC,
            "orden_general_nombre": "OG-ERP-5510.pdf",
            "po_nombre": "PO-QZ-5510.pdf",
            "slack_link": "https://slack.com/archives/C01COMPRAS/p1724000003",
            "invoice_p01_nombre": "INV-QZ-5510.pdf",
            "comprobante_p01_nombre": "PAY-QZ-5510.pdf",
            "invoice_p02_nombre": "INV-JLC-5510.pdf",
            "comprobante_p02_nombre": "PAY-JLC-5510.pdf",
            "fecha_inicio": fmt_dt(inicio_003),
            "etapa": ETAPA_ENVIO_PERU,
            "created_at": fmt_dt(inicio_003),
            "orden_bom_at": fmt_dt(orden_003),
            "invoice_p01_paid_at": fmt_dt(pago_p01_003),
            "acopio_at": fmt_dt(acopio_003),
            "transito_p2_at": fmt_dt(transito_003),
            "llego_p2_at": fmt_dt(llego_003),
            "invoice_p02_paid_at": fmt_dt(pago_p02_003),
            "produccion_at": fmt_dt(prod_003),
            "envio_at": fmt_dt(envio_003),
            "created_by": ROL_SOLICITUD,
        },
        [
            (ROL_SOLICITUD, "Solicitud creada", "Orden General ERP", inicio_003),
            (ROL_LOGISTICA, "Orden BOM generada", "PO adjunta", orden_003),
            (ROL_FINANZAS, "Invoice P01 pagado", "Transferencia QZ", pago_p01_003),
            (ROL_LOGISTICA, "Acopio P01", "Registrado en QZ", acopio_003),
            ("sistema", "Llegada a P02", "Tránsito 4 días", llego_003),
            (ROL_FINANZAS, "Invoice P02 pagado", "Transferencia JLC", pago_p02_003),
            (ROL_LOGISTICA, "En producción P02", "21 días de producción", prod_003),
            (
                ROL_LOGISTICA,
                "Tránsito a Perú",
                "Despacho internacional · 7 días estimados",
                envio_003,
            ),
        ],
    )

    # BOM-2026-004 · espera pago invoice P01
    inicio_004 = t0 - timedelta(days=3)
    orden_004 = t0 - timedelta(hours=10)
    _insert_order(
        conn,
        {
            "bom_code": "BOM-2026-004",
            "descripcion": "Mainboards control residencial — lote de reposición",
            "sku": "MB-RES-REP",
            "cantidad": 320,
            "proveedor_01": PROVEEDOR_QZ,
            "proveedor_02": PROVEEDOR_JLC,
            "orden_general_nombre": "OG-ERP-9102.pdf",
            "po_nombre": "PO-QZ-9102.pdf",
            "slack_link": "https://slack.com/archives/C01COMPRAS/p1726000004",
            "invoice_p01_nombre": "INV-QZ-9102.pdf",
            "fecha_inicio": fmt_dt(inicio_004),
            "etapa": ETAPA_PAGO_INVOICE_P01,
            "created_at": fmt_dt(inicio_004),
            "orden_bom_at": fmt_dt(orden_004),
            "created_by": ROL_SOLICITUD,
        },
        [
            (ROL_SOLICITUD, "Solicitud creada", "OG-ERP-9102.pdf", inicio_004),
            (
                ROL_LOGISTICA,
                "Orden BOM generada",
                "PO-QZ-9102.pdf · pendiente pago invoice P01 (SLA 2 días)",
                orden_004,
            ),
        ],
    )


def fetch_orders() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def fetch_events(order_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM events
            WHERE order_id = ?
            ORDER BY created_at, id
            """,
            (order_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def next_bom_code() -> str:
    year = datetime.now().year
    with get_conn() as conn:
        row = conn.execute(
            "SELECT bom_code FROM orders WHERE bom_code LIKE ? ORDER BY bom_code DESC LIMIT 1",
            (f"BOM-{year}-%",),
        ).fetchone()
    if not row:
        return f"BOM-{year}-001"
    seq = int(row["bom_code"].split("-")[-1]) + 1
    return f"BOM-{year}-{seq:03d}"


def update_order(order_id: int, fields: dict) -> None:
    assignments = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values()) + [order_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE orders SET {assignments} WHERE id = ?", values)


def add_event(order_id: int, actor: str, accion: str, detalle: str, cuando: datetime) -> None:
    with get_conn() as conn:
        _log_event(conn, order_id, actor, accion, detalle, cuando)


# ---------------------------------------------------------------------------
# Motor de workflow / SLA
# ---------------------------------------------------------------------------


def apply_automatic_transitions(ahora: datetime) -> list[str]:
    """Avanza tránsito P01→P02 (4d) y llegada a aduanas tras envío (7d)."""
    avisos: list[str] = []
    for order in fetch_orders():
        etapa = order["etapa"]
        if etapa == ETAPA_TRANSITO_P2 and order["transito_p2_at"]:
            inicio = parse_dt(order["transito_p2_at"])
            if inicio and dias_entre(inicio, ahora) >= DURACION_TRANSITO_P2:
                update_order(
                    order["id"],
                    {
                        "etapa": ETAPA_PAGO_INVOICE_P02,
                        "llego_p2_at": order["llego_p2_at"] or fmt_dt(ahora),
                    },
                )
                add_event(
                    order["id"],
                    "sistema",
                    "Llegada a Proveedor 02 (JLC)",
                    "Tránsito automático de 4 días completado. Pendiente pago de invoice P02.",
                    ahora,
                )
                avisos.append(f"{order['bom_code']}: llegó a P02 (JLC)")

        if etapa == ETAPA_ENVIO_PERU and order["envio_at"]:
            inicio = parse_dt(order["envio_at"])
            if inicio and dias_entre(inicio, ahora) >= DURACION_ENVIO:
                update_order(
                    order["id"],
                    {
                        "etapa": ETAPA_ADUANAS,
                        "aduanas_at": order["aduanas_at"] or fmt_dt(ahora),
                    },
                )
                add_event(
                    order["id"],
                    "sistema",
                    "Carga en aduanas Lima",
                    "Tránsito de 7 días completado. Pendiente pago de aranceles (SLA 7 días).",
                    ahora,
                )
                avisos.append(f"{order['bom_code']}: llegó a aduanas Lima")
    return avisos


def _sla_from(
    tipo: str,
    inicio: datetime | None,
    dias: float,
    ahora: datetime,
) -> dict | None:
    if inicio is None:
        return None
    limite = inicio + timedelta(days=dias)
    restante = dias_entre(ahora, limite)
    return {
        "tipo": tipo,
        "horas_restantes": (restante or 0) * 24,
        "dias_restantes": restante,
        "vencido": ahora > limite,
        "limite": limite,
    }


def sla_activo(order: dict, ahora: datetime) -> dict | None:
    etapa = order["etapa"]

    if etapa == ETAPA_ORDEN_BOM:
        return _sla_from(
            "Orden BOM",
            parse_dt(order["created_at"]),
            SLA_ORDEN_BOM_DIAS,
            ahora,
        )
    if etapa == ETAPA_PAGO_INVOICE_P01:
        return _sla_from(
            "Invoice P01",
            parse_dt(order["orden_bom_at"]) or parse_dt(order["created_at"]),
            SLA_INVOICE_P01_DIAS,
            ahora,
        )
    if etapa == ETAPA_ACOPIO_P01:
        return _sla_from(
            "Acopio P01",
            parse_dt(order["invoice_p01_paid_at"]),
            SLA_ACOPIO_DIAS,
            ahora,
        )
    if etapa == ETAPA_PAGO_INVOICE_P02:
        return _sla_from(
            "Invoice P02",
            parse_dt(order["llego_p2_at"]) or parse_dt(order["transito_p2_at"]),
            SLA_INVOICE_P02_DIAS,
            ahora,
        )
    if etapa == ETAPA_PRODUCCION_P02 and not order["produccion_at"]:
        return _sla_from(
            "Producción P02",
            parse_dt(order["invoice_p02_paid_at"]),
            SLA_PRODUCCION_DIAS,
            ahora,
        )
    if etapa == ETAPA_PRODUCCION_P02 and order["produccion_at"]:
        return _sla_from(
            "Envío a Perú",
            parse_dt(order["produccion_at"]),
            SLA_ENVIO_DIAS,
            ahora,
        )
    if etapa == ETAPA_ENVIO_PERU and not order["envio_at"]:
        return _sla_from(
            "Envío a Perú",
            parse_dt(order["produccion_at"]),
            SLA_ENVIO_DIAS,
            ahora,
        )
    if etapa == ETAPA_ADUANAS:
        return _sla_from(
            "Aranceles",
            parse_dt(order["aduanas_at"]),
            SLA_ARANCELES_DIAS,
            ahora,
        )
    if etapa == ETAPA_TALLER and not order["taller_at"]:
        return _sla_from(
            "Recepción taller",
            parse_dt(order["aranceles_paid_at"]),
            SLA_TALLER_DIAS,
            ahora,
        )
    return None


def conteo_fase(order: dict, ahora: datetime) -> str | None:
    etapa = order["etapa"]
    if etapa == ETAPA_TRANSITO_P2 and order["transito_p2_at"]:
        transcurridos = dias_entre(parse_dt(order["transito_p2_at"]), ahora) or 0
        resto = max(0, DURACION_TRANSITO_P2 - transcurridos)
        return (
            f"Tránsito a P02 · día {min(int(transcurridos) + 1, DURACION_TRANSITO_P2)} "
            f"de {DURACION_TRANSITO_P2} · restan {resto:.1f} d"
        )
    if etapa == ETAPA_PRODUCCION_P02 and order["produccion_at"]:
        transcurridos = dias_entre(parse_dt(order["produccion_at"]), ahora) or 0
        resto = max(0, DURACION_PRODUCCION_P02 - transcurridos)
        return (
            f"Producción JLC · día {min(int(transcurridos) + 1, DURACION_PRODUCCION_P02)} "
            f"de {DURACION_PRODUCCION_P02} · restan {resto:.1f} d"
        )
    if etapa == ETAPA_ENVIO_PERU and order["envio_at"]:
        transcurridos = dias_entre(parse_dt(order["envio_at"]), ahora) or 0
        resto = max(0, DURACION_ENVIO - transcurridos)
        estado = "llegada estimada cumplida" if resto == 0 else f"restan {resto:.1f} d"
        return (
            f"Tránsito a Perú · día {min(int(transcurridos) + 1, DURACION_ENVIO)} "
            f"de {DURACION_ENVIO} · {estado}"
        )
    return None


def region_de(order: dict) -> str:
    return ETAPAS[order["etapa"]]["region"]


def puede_actuar(rol: str, order: dict) -> bool:
    if rol not in ROLES_VALIDOS:
        return False
    etapa = order["etapa"]
    if rol == ROL_SOLICITUD:
        return False  # solo crea solicitudes en el tab Alta
    if rol == ROL_LOGISTICA:
        if etapa == ETAPA_ORDEN_BOM:
            return True
        if etapa == ETAPA_ACOPIO_P01:
            return True
        if etapa == ETAPA_PRODUCCION_P02:
            return True
        return False
    if rol == ROL_FINANZAS:
        return etapa in {
            ETAPA_PAGO_INVOICE_P01,
            ETAPA_PAGO_INVOICE_P02,
            ETAPA_ADUANAS,
        }
    if rol == ROL_TALLER:
        return etapa == ETAPA_TALLER and not order["taller_at"]
    return False


def avanzar_etapa(
    order: dict,
    rol: str,
    ahora: datetime,
    accion: str,
    adjunto: str | None = None,
) -> str:
    if rol not in ROLES_VALIDOS:
        return "Rol no autorizado."

    oid = order["id"]
    etapa = order["etapa"]

    if accion == "generar_orden_bom":
        if rol != ROL_LOGISTICA or etapa != ETAPA_ORDEN_BOM:
            return "Esta acción corresponde a Adrián en la etapa de Orden BOM."
        fields = {
            "etapa": ETAPA_PAGO_INVOICE_P01,
            "orden_bom_at": fmt_dt(ahora),
        }
        if adjunto:
            if adjunto.startswith("http"):
                fields["slack_link"] = adjunto
            else:
                fields["po_nombre"] = adjunto
        update_order(oid, fields)
        add_event(
            oid,
            rol,
            "Orden BOM generada",
            f"Documento: {adjunto or '—'}. Pendiente pago invoice P01 (SLA 2 días).",
            ahora,
        )
        return "Orden BOM registrada. Finanzas tiene 2 días de SLA para pagar el invoice P01."

    if accion == "pagar_invoice_p01":
        if rol != ROL_FINANZAS or etapa != ETAPA_PAGO_INVOICE_P01:
            return "Esta acción corresponde a Julio en el pago de invoice P01."
        fields = {
            "etapa": ETAPA_ACOPIO_P01,
            "invoice_p01_paid_at": fmt_dt(ahora),
        }
        if adjunto:
            fields["comprobante_p01_nombre"] = adjunto
        update_order(oid, fields)
        add_event(
            oid,
            rol,
            "Invoice P01 pagado",
            f"Comprobante: {adjunto or '—'}. Pendiente acopio QZ (SLA 2 días).",
            ahora,
        )
        return "Invoice P01 pagado. Adrián tiene 2 días de SLA para confirmar acopio en QZ."

    if accion == "marcar_acopio":
        if rol != ROL_LOGISTICA or etapa != ETAPA_ACOPIO_P01:
            return "Esta acción corresponde a Adrián en acopio P01 (QZ)."
        update_order(
            oid,
            {
                "etapa": ETAPA_TRANSITO_P2,
                "acopio_at": fmt_dt(ahora),
                "transito_p2_at": fmt_dt(ahora),
            },
        )
        add_event(
            oid,
            rol,
            "Pedido registrado / en acopio",
            "Inicia tránsito automático de 4 días hacia JLC (P02).",
            ahora,
        )
        return "Acopio QZ confirmado. Inicia tránsito automático de 4 días a P02 (JLC)."

    if accion == "pagar_invoice_p02":
        if rol != ROL_FINANZAS or etapa != ETAPA_PAGO_INVOICE_P02:
            return "Esta acción corresponde a Julio en el pago de invoice P02."
        fields = {
            "etapa": ETAPA_PRODUCCION_P02,
            "invoice_p02_paid_at": fmt_dt(ahora),
        }
        if adjunto:
            fields["comprobante_p02_nombre"] = adjunto
        update_order(oid, fields)
        add_event(
            oid,
            rol,
            "Invoice P02 pagado",
            f"Comprobante: {adjunto or '—'}. Pendiente marcar producción JLC (SLA 2 días).",
            ahora,
        )
        return "Invoice P02 pagado. Adrián tiene 2 días de SLA para marcar producción en JLC."

    if accion == "marcar_produccion":
        if rol != ROL_LOGISTICA or etapa != ETAPA_PRODUCCION_P02:
            return "Esta acción corresponde a Adrián en producción P02 (JLC)."
        if order["produccion_at"]:
            return "La producción ya fue registrada."
        update_order(oid, {"produccion_at": fmt_dt(ahora)})
        add_event(
            oid,
            rol,
            "En producción P02",
            "Fase estimada de 21 días. Luego marcar tránsito a Perú (SLA 21 días).",
            ahora,
        )
        return "Producción JLC registrada. Fase estimada de 21 días en sistema."

    if accion == "enviar_peru":
        if rol != ROL_LOGISTICA or etapa != ETAPA_PRODUCCION_P02:
            return "El despacho lo registra Adrián durante la producción P02."
        if not order["produccion_at"]:
            return "Primero marque 'en Producción'."
        update_order(oid, {"etapa": ETAPA_ENVIO_PERU, "envio_at": fmt_dt(ahora)})
        add_event(
            oid,
            rol,
            "Tránsito a Perú",
            "Tránsito estimado de 7 días hasta aduanas Lima.",
            ahora,
        )
        return "Despacho registrado. 7 días estimados hasta aduanas Lima."

    if accion == "pagar_aranceles":
        if rol != ROL_FINANZAS or etapa != ETAPA_ADUANAS:
            return "El pago de aranceles corresponde a Julio con carga en aduanas."
        fields = {
            "etapa": ETAPA_TALLER,
            "aranceles_paid_at": fmt_dt(ahora),
        }
        if adjunto:
            fields["comprobante_aranceles_nombre"] = adjunto
        update_order(oid, fields)
        add_event(
            oid,
            rol,
            "Aranceles pagados",
            f"Comprobante aranceles/DHL: {adjunto or '—'}. Pendiente recepción en taller (SLA 1 día).",
            ahora,
        )
        return "Aranceles pagados. Lucho tiene 1 día de SLA para confirmar recepción en taller."

    if accion == "recibir_taller":
        if rol != ROL_TALLER or etapa != ETAPA_TALLER:
            return "La recepción la confirma Lucho en taller Lima."
        if order["taller_at"]:
            return "La recepción ya fue registrada."
        update_order(oid, {"taller_at": fmt_dt(ahora)})
        add_event(
            oid,
            rol,
            "Recepción en taller",
            "Carga recibida en taller Lima · lista para integración.",
            ahora,
        )
        return "Recepción confirmada. Carga lista para integración en taller Lima."

    return "Acción no reconocida."


# ---------------------------------------------------------------------------
# Métricas y gráficos
# ---------------------------------------------------------------------------


def orders_frame(orders: list[dict], ahora: datetime) -> pd.DataFrame:
    rows = []
    for order in orders:
        sla = sla_activo(order, ahora)
        etapa_meta = ETAPAS[order["etapa"]]
        completada = bool(order["taller_at"]) and order["etapa"] == ETAPA_TALLER
        progreso = 1.0 if completada else etapa_meta["orden"] / TOTAL_ETAPAS
        rows.append(
            {
                "BOM": order["bom_code"],
                "Descripción": order["descripcion"],
                "SKU": order["sku"],
                "Cantidad": order["cantidad"],
                "Proveedor 01": order["proveedor_01"],
                "Proveedor 02": order["proveedor_02"],
                "Etapa": etapa_meta["nombre"],
                "Región": etapa_meta["region"],
                "Progreso": progreso,
                "Fecha inicio": parse_dt(order["fecha_inicio"]),
                "SLA": (
                    "Vencido"
                    if sla and sla["vencido"]
                    else (f"{sla['dias_restantes']:.1f} d" if sla else "—")
                ),
                "Docs": order["po_nombre"]
                or order["orden_general_nombre"]
                or order["slack_link"]
                or "—",
            }
        )
    return pd.DataFrame(rows)


def kpis(orders: list[dict], ahora: datetime) -> dict[str, int]:
    return {
        "total": len(orders),
        "lima": sum(1 for o in orders if region_de(o) == "lima"),
        "china": sum(1 for o in orders if region_de(o) == "china"),
        "transito": sum(1 for o in orders if region_de(o) == "transito"),
        "aduanas": sum(1 for o in orders if region_de(o) == "aduanas"),
        "taller": sum(
            1 for o in orders if region_de(o) == "taller" and o.get("taller_at")
        ),
        "sla": sum(1 for o in orders if (s := sla_activo(o, ahora)) and s["vencido"]),
    }


def build_gantt(orders: list[dict], ahora: datetime) -> pd.DataFrame:
    plan = [
        ("Orden BOM PCB", "created_at", "orden_bom_at", SLA_ORDEN_BOM_DIAS, ETAPA_ORDEN_BOM),
        ("Pago invoice P01", "orden_bom_at", "invoice_p01_paid_at", SLA_INVOICE_P01_DIAS, ETAPA_PAGO_INVOICE_P01),
        ("Acopio P01 (QZ)", "invoice_p01_paid_at", "acopio_at", SLA_ACOPIO_DIAS, ETAPA_ACOPIO_P01),
        ("Tránsito a P02", "transito_p2_at", "llego_p2_at", DURACION_TRANSITO_P2, ETAPA_TRANSITO_P2),
        ("Pago invoice P02", "llego_p2_at", "invoice_p02_paid_at", SLA_INVOICE_P02_DIAS, ETAPA_PAGO_INVOICE_P02),
        ("Producción P02 (JLC)", "produccion_at", "envio_at", DURACION_PRODUCCION_P02, ETAPA_PRODUCCION_P02),
        ("Envío a Perú", "envio_at", "aduanas_at", DURACION_ENVIO, ETAPA_ENVIO_PERU),
        ("Aduanas / aranceles", "aduanas_at", "aranceles_paid_at", SLA_ARANCELES_DIAS, ETAPA_ADUANAS),
        ("Recepción taller", "aranceles_paid_at", "taller_at", SLA_TALLER_DIAS, ETAPA_TALLER),
    ]
    rows: list[dict] = []
    for order in orders:
        cursor: datetime | None = parse_dt(order["fecha_inicio"]) or parse_dt(order["created_at"])
        for nombre, start_key, end_key, duracion, _etapa_ref in plan:
            start = parse_dt(order.get(start_key))
            end = parse_dt(order.get(end_key))
            if start is None:
                if cursor is None:
                    continue
                start = cursor
                end = start + timedelta(days=duracion)
                estado = "Planificada"
            elif end is None:
                planned_end = start + timedelta(days=duracion)
                end = max(planned_end, ahora) if ahora >= start else planned_end
                estado = "En curso"
            else:
                estado = "Completada"

            end = asegurar_span(start, end)
            rows.append(
                {
                    "BOM": order["bom_code"],
                    "Fase": nombre,
                    "Inicio": start,
                    "Fin": end,
                    "Estado": estado,
                }
            )
            cursor = end
    return pd.DataFrame(rows)


def style_plotly(fig: go.Figure) -> go.Figure:
    dark = st.context.theme.type == "dark"
    fig.update_layout(
        template="plotly_dark" if dark else "plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=16, r=16, t=48, b=16),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        font=dict(family="Inter, Segoe UI, sans-serif", size=13),
    )
    return fig


def gantt_chart(gantt_df: pd.DataFrame, ahora: datetime) -> go.Figure:
    if gantt_df.empty:
        fig = go.Figure()
        fig.update_layout(title="Sin órdenes para graficar")
        return style_plotly(fig)

    fig = px.timeline(
        gantt_df,
        x_start="Inicio",
        x_end="Fin",
        y="BOM",
        color="Fase",
        color_discrete_map=COLORES_FASE,
        hover_data=["Estado", "Fase"],
        title="Ciclo de vida China → Lima (10 etapas)",
    )
    fig.update_yaxes(autorange="reversed", title="")
    fig.update_xaxes(title="Fecha operativa")
    fig.update_traces(marker_line_width=0)
    # add_vline + datetime falla en Plotly al calcular la anotación;
    # usamos shape + annotation explícitos.
    fig.add_shape(
        type="line",
        x0=ahora,
        x1=ahora,
        y0=0,
        y1=1,
        xref="x",
        yref="paper",
        line=dict(width=1.5, dash="dot", color="#B45309"),
    )
    fig.add_annotation(
        x=ahora,
        y=1.02,
        yref="paper",
        text="Hoy",
        showarrow=False,
        font=dict(color="#B45309", size=12),
    )
    return style_plotly(fig)


def donut_chart(orders: list[dict]) -> go.Figure:
    if not orders:
        fig = go.Figure()
        fig.update_layout(title="Sin órdenes")
        return style_plotly(fig)

    counts = (
        pd.Series([ETAPAS[o["etapa"]]["nombre"] for o in orders])
        .value_counts()
        .reset_index()
    )
    counts.columns = ["Fase", "Órdenes"]
    palette = [
        "#64748B",
        "#0F766E",
        "#B45309",
        "#0F6E6B",
        "#0E7490",
        "#C2410C",
        "#1D4E89",
        "#C45C26",
        "#7C3AED",
        "#15803D",
    ]
    color_map = {
        ETAPAS[k]["nombre"]: color for k, color in zip(ETAPAS, palette)
    }
    fig = go.Figure(
        data=[
            go.Pie(
                labels=counts["Fase"],
                values=counts["Órdenes"],
                hole=0.58,
                marker=dict(
                    colors=[color_map.get(name, "#5B6B6A") for name in counts["Fase"]],
                    line=dict(width=0),
                ),
                textinfo="label+value",
                hovertemplate="%{label}<br>%{value} órdenes<extra></extra>",
            )
        ]
    )
    fig.update_layout(title="Órdenes por fase")
    return style_plotly(fig)


def bar_chart(orders: list[dict]) -> go.Figure:
    regiones = {
        "lima": "En Lima (solicitud/BOM)",
        "china": "En China",
        "transito": "En tránsito a Perú",
        "aduanas": "En aduanas",
        "taller": "En taller",
    }
    data = (
        pd.Series([regiones[region_de(o)] for o in orders])
        .value_counts()
        .reindex(list(regiones.values()), fill_value=0)
        .reset_index()
    )
    data.columns = ["Ubicación", "Órdenes"]
    fig = px.bar(
        data,
        x="Ubicación",
        y="Órdenes",
        color="Ubicación",
        color_discrete_sequence=["#64748B", "#0F6E6B", "#C45C26", "#7C3AED", "#15803D"],
        title="Inventario de órdenes por ubicación",
    )
    fig.update_layout(showlegend=False)
    fig.update_yaxes(dtick=1)
    return style_plotly(fig)


# ---------------------------------------------------------------------------
# Acciones de UI
# ---------------------------------------------------------------------------


def guardar_pdf(uploaded, bom_code: str, prefijo: str = "") -> str | None:
    if uploaded is None:
        return None
    prefix = f"{prefijo}_" if prefijo else ""
    safe_name = f"{bom_code}_{prefix}{uploaded.name}".replace(" ", "_")
    destino = UPLOAD_DIR / safe_name
    destino.write_bytes(uploaded.getbuffer())
    return safe_name


def crear_solicitud(form: dict, ahora: datetime) -> str:
    bom = next_bom_code()
    orden_general = guardar_pdf(form["pdf"], bom, "OG")
    payload = {
        "bom_code": bom,
        "descripcion": form["descripcion"].strip(),
        "sku": form["sku"].strip() or None,
        "cantidad": int(form["cantidad"]),
        "proveedor_01": form["proveedor_01"],
        "proveedor_02": form["proveedor_02"],
        "orden_general_nombre": orden_general,
        "fecha_inicio": fmt_dt(datetime.combine(form["fecha_inicio"], ahora.time())),
        "etapa": ETAPA_ORDEN_BOM,
        "created_at": fmt_dt(ahora),
        "created_by": ROL_SOLICITUD,
    }
    with get_conn() as conn:
        _insert_order(
            conn,
            payload,
            [
                (
                    ROL_SOLICITUD,
                    "Solicitud creada",
                    f"Orden General: {orden_general or 'sin PDF'}. "
                    "Pendiente Orden BOM (SLA 4 días).",
                    ahora,
                )
            ],
        )
    return bom


# ---------------------------------------------------------------------------
# Interfaz
# ---------------------------------------------------------------------------


def render_sidebar() -> str:
    with st.sidebar:
        st.header("Control de sesión")
        st.caption("Simula el actor que ejecuta cada acción del flujo.")
        rol = st.radio(
            "Rol activo",
            options=list(ROLES.keys()),
            format_func=lambda key: ROLES[key]["label"],
            key="rol_activo",
        )
        if rol not in ROLES_VALIDOS:
            st.error("Rol no válido.")
            st.stop()

        meta = ROLES[rol]
        st.badge(meta["corto"], icon=meta["icon"], color="green")
        st.caption(
            "Las casillas y botones del panel de gestión se habilitan "
            "solo para el rol responsable de la etapa."
        )

        st.subheader("Reloj operativo")
        ahora = now_operativo()
        st.metric("Fecha simulada", ahora.strftime("%d %b %Y"), border=True)
        with st.container(horizontal=True):
            if st.button("Simular +1 día", icon=":material/forward:", width="stretch"):
                st.session_state.sim_days = int(st.session_state.get("sim_days", 0)) + 1
                st.rerun()
            if st.button("Hoy real", icon=":material/today:", width="stretch"):
                st.session_state.sim_days = 0
                st.rerun()
        st.caption(
            "Usa el avance de día para disparar el tránsito QZ→JLC (4 días) "
            "y la llegada a aduanas tras el envío (7 días)."
        )

        with st.expander("Datos de demostración", icon=":material/database:"):
            st.caption(
                "Cuatro BOM de prueba en etapas distintas del proceso de 10 pasos."
            )
            if st.button("Restablecer demo", icon=":material/restart_alt:"):
                reset_demo()
                st.toast("Demo restablecida", icon=":material/check:")
                st.rerun()

        st.caption("Metta Dashboard · MVP trazabilidad v0.2 · 10 etapas")
    return rol


def render_kpis(metrics: dict[str, int]) -> None:
    with st.container(horizontal=True):
        st.metric("Total de órdenes", metrics["total"], border=True)
        st.metric("Solicitud / BOM (Lima)", metrics["lima"], border=True)
        st.metric("Órdenes en China", metrics["china"], border=True)
    with st.container(horizontal=True):
        st.metric("En tránsito a Perú", metrics["transito"], border=True)
        st.metric("En aduanas", metrics["aduanas"], border=True)
        st.metric("Completadas en taller", metrics["taller"], border=True)
    sla_delta = "Sin alertas" if metrics["sla"] == 0 else f"{metrics['sla']} vencidas"
    st.metric(
        "Alertas de SLA vencido",
        metrics["sla"],
        delta=sla_delta,
        delta_color="off" if metrics["sla"] == 0 else "inverse",
        border=True,
    )


def render_dashboard(orders: list[dict], ahora: datetime) -> None:
    metrics = kpis(orders, ahora)
    render_kpis(metrics)

    if metrics["sla"]:
        vencidas = [
            o["bom_code"]
            for o in orders
            if (s := sla_activo(o, ahora)) and s["vencido"]
        ]
        st.warning(
            f"SLA vencido en: {', '.join(vencidas)}",
            icon=":material/warning:",
        )

    gantt_df = build_gantt(orders, ahora)
    left, right = st.columns((1.65, 1), gap="large")
    with left:
        with st.container(border=True, height="stretch"):
            st.plotly_chart(gantt_chart(gantt_df, ahora), width="stretch")
    with right:
        with st.container(border=True, height="stretch"):
            st.plotly_chart(donut_chart(orders), width="stretch")

    with st.container(border=True):
        st.plotly_chart(bar_chart(orders), width="stretch")

    with st.container(border=True):
        st.subheader("Inventario de órdenes")
        df = orders_frame(orders, ahora)
        st.dataframe(
            df,
            hide_index=True,
            width="stretch",
            column_config={
                "Progreso": st.column_config.ProgressColumn(
                    "Progreso",
                    min_value=0,
                    max_value=1,
                    format="percent",
                ),
                "Fecha inicio": st.column_config.DatetimeColumn(
                    "Fecha inicio",
                    format="D MMM YYYY",
                ),
                "Cantidad": st.column_config.NumberColumn("Cantidad", format="%d"),
                "Docs": st.column_config.TextColumn("Docs"),
            },
        )


def _sla_markdown(sla: dict) -> None:
    if sla["vencido"]:
        demora = abs(sla["dias_restantes"] or 0)
        st.markdown(
            f":red[**SLA {sla['tipo']} vencido** · {demora:.1f} días / "
            f"{abs(sla['horas_restantes']):.0f} h de retraso]"
        )
    else:
        st.markdown(
            f":orange[**SLA {sla['tipo']} restante:** {sla['dias_restantes']:.1f} días "
            f"({sla['horas_restantes']:.0f} h) · límite {sla['limite'].strftime('%d %b %H:%M')}]"
        )


def render_order_card(order: dict, rol: str, ahora: datetime) -> None:
    etapa_meta = ETAPAS[order["etapa"]]
    sla = sla_activo(order, ahora)
    badge_color = {
        "lima": "gray",
        "china": "blue",
        "transito": "orange",
        "aduanas": "violet",
        "taller": "green",
    }[etapa_meta["region"]]

    with st.container(border=True):
        title_col, badge_col = st.columns((3, 1))
        with title_col:
            st.subheader(order["bom_code"])
            st.caption(order["descripcion"])
        with badge_col:
            st.badge(
                f"Etapa {etapa_meta['orden']}/{TOTAL_ETAPAS}",
                icon=":material/flag:",
                color=badge_color,
            )
            st.badge(etapa_meta["nombre"], color=badge_color)

        meta1, meta2, meta3 = st.columns(3)
        meta1.markdown(f"**Proveedor 01 (QZ)**  \n{order['proveedor_01']}")
        meta2.markdown(f"**Proveedor 02 (JLC)**  \n{order['proveedor_02']}")
        meta3.markdown(f"**Cantidad**  \n{order['cantidad']} · {order['sku'] or 's/SKU'}")

        refs = []
        if order.get("orden_general_nombre"):
            refs.append(f"OG: `{order['orden_general_nombre']}`")
        if order.get("po_nombre"):
            refs.append(f"PO: `{order['po_nombre']}`")
        if order.get("slack_link"):
            refs.append(f"[Hilo Slack]({order['slack_link']})")
        if order.get("comprobante_p01_nombre"):
            refs.append(f"Pago P01: `{order['comprobante_p01_nombre']}`")
        if order.get("comprobante_p02_nombre"):
            refs.append(f"Pago P02: `{order['comprobante_p02_nombre']}`")
        if order.get("comprobante_aranceles_nombre"):
            refs.append(f"Aranceles: `{order['comprobante_aranceles_nombre']}`")
        if refs:
            st.caption(" · ".join(refs))

        st.caption(etapa_meta["detalle"])
        countdown = conteo_fase(order, ahora)
        if countdown:
            st.info(countdown, icon=":material/schedule:")
        if sla:
            _sla_markdown(sla)

        _render_acciones(order, rol, ahora)

        eventos = fetch_events(order["id"])
        with st.expander("Bitácora de trazabilidad", icon=":material/history:"):
            if not eventos:
                st.caption("Sin eventos.")
            else:
                bitacora = pd.DataFrame(
                    [
                        {
                            "Cuando": ev["created_at"],
                            "Actor": ROLES.get(ev["actor"], {}).get("corto", ev["actor"]),
                            "Acción": ev["accion"],
                            "Detalle": ev["detalle"],
                        }
                        for ev in eventos
                    ]
                )
                st.dataframe(bitacora, hide_index=True, width="stretch")


def _render_acciones(order: dict, rol: str, ahora: datetime) -> None:
    etapa = order["etapa"]
    oid = order["id"]

    if etapa == ETAPA_TALLER and order["taller_at"]:
        st.success(
            "Carga recibida en taller Lima · lista para integración.",
            icon=":material/factory:",
        )
        return

    if etapa == ETAPA_ORDEN_BOM:
        habilitado = rol == ROL_LOGISTICA
        if not habilitado:
            st.caption("Acción bloqueada · requiere Usuario 02 (Adrián).")
        pdf = st.file_uploader(
            "Purchase Order (PDF)",
            type=["pdf"],
            key=f"po_{oid}",
            disabled=not habilitado,
        )
        slack = st.text_input(
            "O enlace de Slack",
            key=f"slack_{oid}",
            disabled=not habilitado,
            placeholder="https://slack.com/archives/...",
        )
        if st.button(
            "Generar orden BOM PCB",
            key=f"btn_bom_{oid}",
            icon=":material/description:",
            type="primary",
            disabled=not habilitado,
        ):
            if pdf is None and not (slack or "").strip():
                st.error("Adjunta la PO o un enlace de Slack.")
                return
            adjunto = guardar_pdf(pdf, order["bom_code"], "PO") if pdf else slack.strip()
            mensaje = avanzar_etapa(order, rol, ahora, "generar_orden_bom", adjunto)
            st.toast(mensaje, icon=":material/check:")
            st.rerun()
        return

    if etapa == ETAPA_PAGO_INVOICE_P01:
        habilitado = rol == ROL_FINANZAS
        if not habilitado:
            st.caption("Acción bloqueada · requiere Usuario 03 (Julio).")
        checked = st.checkbox(
            "Invoice pagado (P01 / QZ)",
            key=f"chk_inv_p01_{oid}",
            disabled=not habilitado,
        )
        pdf = st.file_uploader(
            "Comprobante de pago (PDF)",
            type=["pdf"],
            key=f"pay_p01_{oid}",
            disabled=not habilitado,
        )
        if st.button(
            "Registrar pago de invoice P01",
            key=f"btn_inv_p01_{oid}",
            icon=":material/payments:",
            type="primary",
            disabled=not (habilitado and checked),
        ):
            if pdf is None:
                st.error("Adjunta el comprobante de pago.")
                return
            adjunto = guardar_pdf(pdf, order["bom_code"], "PAY_P01")
            mensaje = avanzar_etapa(order, rol, ahora, "pagar_invoice_p01", adjunto)
            st.toast(mensaje, icon=":material/check:")
            st.rerun()
        return

    if etapa == ETAPA_ACOPIO_P01:
        habilitado = rol == ROL_LOGISTICA
        if not habilitado:
            st.caption("Acción bloqueada · requiere Usuario 02 (Adrián) · SLA 2 días.")
        checked = st.checkbox(
            "Pedido registrado / en acopio (BOM)",
            key=f"chk_acopio_{oid}",
            disabled=not habilitado,
        )
        if st.button(
            "Confirmar acopio P01 (QZ)",
            key=f"btn_acopio_{oid}",
            icon=":material/inventory_2:",
            type="primary",
            disabled=not (habilitado and checked),
        ):
            mensaje = avanzar_etapa(order, rol, ahora, "marcar_acopio")
            st.toast(mensaje, icon=":material/check:")
            st.rerun()
        return

    if etapa == ETAPA_TRANSITO_P2:
        st.caption(
            "Etapa automática. El sistema marcará la llegada a P02 (JLC) a los 4 días."
        )
        return

    if etapa == ETAPA_PAGO_INVOICE_P02:
        habilitado = rol == ROL_FINANZAS
        if not habilitado:
            st.caption("Acción bloqueada · requiere Usuario 03 (Julio) · SLA 1 día.")
        checked = st.checkbox(
            "Invoice pagado (P02 / JLC)",
            key=f"chk_inv_p02_{oid}",
            disabled=not habilitado,
        )
        pdf = st.file_uploader(
            "Comprobante de pago (PDF)",
            type=["pdf"],
            key=f"pay_p02_{oid}",
            disabled=not habilitado,
        )
        if st.button(
            "Registrar pago de invoice P02",
            key=f"btn_inv_p02_{oid}",
            icon=":material/payments:",
            type="primary",
            disabled=not (habilitado and checked),
        ):
            if pdf is None:
                st.error("Adjunta el comprobante de pago.")
                return
            adjunto = guardar_pdf(pdf, order["bom_code"], "PAY_P02")
            mensaje = avanzar_etapa(order, rol, ahora, "pagar_invoice_p02", adjunto)
            st.toast(mensaje, icon=":material/check:")
            st.rerun()
        return

    if etapa == ETAPA_PRODUCCION_P02:
        habilitado = rol == ROL_LOGISTICA
        if not habilitado:
            st.caption("Acción bloqueada · requiere Usuario 02 (Adrián).")
        if not order["produccion_at"]:
            checked = st.checkbox(
                "En producción (JLC)",
                key=f"chk_prod_{oid}",
                disabled=not habilitado,
            )
            if st.button(
                "Marcar en producción",
                key=f"btn_prod_{oid}",
                icon=":material/precision_manufacturing:",
                type="primary",
                disabled=not (habilitado and checked),
            ):
                mensaje = avanzar_etapa(order, rol, ahora, "marcar_produccion")
                st.toast(mensaje, icon=":material/check:")
                st.rerun()
        else:
            checked = st.checkbox(
                "Tránsito a Perú",
                key=f"chk_ship_{oid}",
                disabled=not habilitado,
            )
            if st.button(
                "Marcar tránsito a Perú",
                key=f"btn_ship_{oid}",
                icon=":material/flight_takeoff:",
                type="primary",
                disabled=not (habilitado and checked),
            ):
                mensaje = avanzar_etapa(order, rol, ahora, "enviar_peru")
                st.toast(mensaje, icon=":material/check:")
                st.rerun()
        return

    if etapa == ETAPA_ENVIO_PERU:
        st.caption(
            "Etapa automática. El sistema marcará llegada a aduanas Lima a los 7 días."
        )
        return

    if etapa == ETAPA_ADUANAS:
        habilitado = rol == ROL_FINANZAS
        if not habilitado:
            st.caption("Acción bloqueada · requiere Usuario 03 (Julio) · SLA 7 días.")
        checked = st.checkbox(
            "Aranceles pagados",
            key=f"chk_tax_{oid}",
            disabled=not habilitado,
        )
        pdf = st.file_uploader(
            "Comprobante aranceles + DHL (PDF)",
            type=["pdf"],
            key=f"pay_tax_{oid}",
            disabled=not habilitado,
        )
        if st.button(
            "Registrar pago de aranceles",
            key=f"btn_tax_{oid}",
            icon=":material/account_balance:",
            type="primary",
            disabled=not (habilitado and checked),
        ):
            if pdf is None:
                st.error("Adjunta el comprobante de aranceles / DHL.")
                return
            adjunto = guardar_pdf(pdf, order["bom_code"], "ARANCEL")
            mensaje = avanzar_etapa(order, rol, ahora, "pagar_aranceles", adjunto)
            st.toast(mensaje, icon=":material/check:")
            st.rerun()
        return

    if etapa == ETAPA_TALLER and not order["taller_at"]:
        habilitado = rol == ROL_TALLER
        if not habilitado:
            st.caption("Acción bloqueada · requiere Usuario 04 (Lucho) · SLA 1 día.")
        checked = st.checkbox(
            "Recepción en taller",
            key=f"chk_taller_{oid}",
            disabled=not habilitado,
        )
        if st.button(
            "Confirmar recepción en taller",
            key=f"btn_taller_{oid}",
            icon=":material/factory:",
            type="primary",
            disabled=not (habilitado and checked),
        ):
            mensaje = avanzar_etapa(order, rol, ahora, "recibir_taller")
            st.toast(mensaje, icon=":material/check:")
            st.rerun()


def render_gestion(orders: list[dict], rol: str, ahora: datetime) -> None:
    activas = [
        o for o in orders if not (o["etapa"] == ETAPA_TALLER and o.get("taller_at"))
    ]
    cerradas = [
        o for o in orders if o["etapa"] == ETAPA_TALLER and o.get("taller_at")
    ]

    pendientes_rol = [o for o in activas if puede_actuar(rol, o)]
    if pendientes_rol:
        st.info(
            f"{len(pendientes_rol)} orden(es) esperan una acción de {ROLES[rol]['corto']}.",
            icon=":material/task_alt:",
        )
    else:
        st.caption(f"No hay acciones pendientes para {ROLES[rol]['corto']}.")

    if not activas:
        st.success("No hay órdenes activas.", icon=":material/check_circle:")
    else:
        st.subheader("Órdenes activas")
        for order in activas:
            render_order_card(order, rol, ahora)

    if cerradas:
        st.subheader("Completadas en taller")
        for order in cerradas:
            render_order_card(order, rol, ahora)


def render_alta(rol: str, ahora: datetime) -> None:
    if rol != ROL_SOLICITUD:
        st.warning(
            "Solo el Usuario 01 (José) puede registrar una nueva solicitud de compra.",
            icon=":material/lock:",
        )
        st.caption("Cambia el rol en la barra lateral para habilitar el formulario.")
        return

    st.subheader("Nueva solicitud de compra")
    st.caption(
        "Adjunta la Orden General del ERP. Adrián tendrá 4 días de SLA "
        "para generar la orden BOM PCB."
    )

    with st.form("nueva_solicitud", border=True):
        descripcion = st.text_input(
            "Descripción del BOM",
            placeholder="Mainboards control IoT v3 — lote de reposición",
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            sku = st.text_input("SKU", placeholder="MB-IOT-V3")
        with c2:
            cantidad = st.number_input("Cantidad", min_value=1, value=100, step=10)
        with c3:
            fecha_inicio = st.date_input("Fecha de inicio", value=ahora.date())

        p1, p2 = st.columns(2)
        with p1:
            proveedor_01 = st.selectbox("Proveedor 01 · Acopio China", PROVEEDORES_01)
        with p2:
            proveedor_02 = st.selectbox("Proveedor 02 · Producción / ensamble", PROVEEDORES_02)

        pdf = st.file_uploader("Orden General del ERP (PDF)", type=["pdf"])

        enviado = st.form_submit_button(
            "Generar solicitud de compra",
            icon=":material/add_box:",
            type="primary",
        )

    if enviado:
        if not descripcion.strip():
            st.error("La descripción es obligatoria.", icon=":material/error:")
            return
        if pdf is None:
            st.error(
                "Adjunta la Orden General del ERP para iniciar la trazabilidad.",
                icon=":material/attach_file:",
            )
            return
        bom = crear_solicitud(
            {
                "descripcion": descripcion,
                "sku": sku,
                "cantidad": cantidad,
                "fecha_inicio": fecha_inicio,
                "proveedor_01": proveedor_01,
                "proveedor_02": proveedor_02,
                "pdf": pdf,
            },
            ahora,
        )
        st.success(
            f"Solicitud **{bom}** creada. Adrián tiene 4 días de SLA "
            "para generar la orden BOM PCB.",
            icon=":material/check_circle:",
        )
        st.balloons()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

st.session_state.setdefault("sim_days", 0)
st.session_state.setdefault("rol_activo", ROL_SOLICITUD)

init_db()
rol_activo = render_sidebar()
ahora = now_operativo()
avisos = apply_automatic_transitions(ahora)
orders = fetch_orders()

st.title("Trazabilidad de producción")
st.caption(
    "Control logístico BOM PCB · China (QZ → JLC) → Lima · "
    f"operando como **{ROLES[rol_activo]['label']}** · "
    f"{ahora.strftime('%d %b %Y %H:%M')}"
)

for aviso in avisos:
    st.toast(aviso, icon=":material/sync:")

dashboard_tab, gestion_tab, alta_tab = st.tabs(
    [
        ":material/space_dashboard: Dashboard principal",
        ":material/tune: Gestión y control",
        ":material/add_box: Nueva solicitud de compra",
    ],
    on_change="rerun",
)

if dashboard_tab.open:
    with dashboard_tab:
        render_dashboard(orders, ahora)

if gestion_tab.open:
    with gestion_tab:
        render_gestion(orders, rol_activo, ahora)

if alta_tab.open:
    with alta_tab:
        render_alta(rol_activo, ahora)
