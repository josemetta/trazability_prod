"""Metta · Trazabilidad logística China → Lima.

MVP Streamlit para el control de órdenes de compra (BOM), pagos con SLA
y seguimiento de producción / tránsito desde China hasta el taller en Lima.
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

DATE_FMT = "%Y-%m-%d %H:%M:%S"

ROL_COMPRAS = "usuario_01"
ROL_FINANZAS = "usuario_02"
ROL_ADUANAS = "usuario_03"
ROLES_VALIDOS = {ROL_COMPRAS, ROL_FINANZAS, ROL_ADUANAS}

ROLES = {
    ROL_COMPRAS: {
        "label": "Usuario 01 · Compras y logística internacional",
        "corto": "Compras / logística",
        "icon": ":material/shopping_cart:",
    },
    ROL_FINANZAS: {
        "label": "Usuario 02 · Finanzas / pagos",
        "corto": "Finanzas",
        "icon": ":material/payments:",
    },
    ROL_ADUANAS: {
        "label": "Usuario 03 · Operaciones y aduanas (Lima)",
        "corto": "Aduanas Lima",
        "icon": ":material/warehouse:",
    },
}

# Etapas de la máquina de estados (orden operativo)
ETAPA_PAGO_INVOICE = "pago_invoice"
ETAPA_PRODUCCION = "produccion_p1"
ETAPA_TRANSITO_P2 = "transito_p2"
ETAPA_ENSAMBLADO = "ensamblado_p2"
ETAPA_ENVIO = "envio_peru"
ETAPA_ADUANAS = "aduanas_lima"
ETAPA_ARANCELES = "pago_aranceles"
ETAPA_TALLER = "taller_lima"

# La etapa 1 (creación) ocurre en el formulario; el BOM nace en etapa 2.
TOTAL_ETAPAS = 9

ETAPAS = {
    ETAPA_PAGO_INVOICE: {
        "orden": 2,
        "nombre": "Pago de invoice",
        "detalle": "Espera de pago al Proveedor 01 · SLA 2 días",
        "region": "china",
    },
    ETAPA_PRODUCCION: {
        "orden": 3,
        "nombre": "Producción Proveedor 01",
        "detalle": "Confirmar pedido registrado / en producción · SLA 3 días",
        "region": "china",
    },
    ETAPA_TRANSITO_P2: {
        "orden": 4,
        "nombre": "Tránsito a Proveedor 02",
        "detalle": "Conteo automático de 12 días",
        "region": "china",
    },
    ETAPA_ENSAMBLADO: {
        "orden": 5,
        "nombre": "Ensamblado Proveedor 02",
        "detalle": "Fase de 13 días de producción",
        "region": "china",
    },
    ETAPA_ENVIO: {
        "orden": 6,
        "nombre": "Envío internacional a Perú",
        "detalle": "Mainboards en tránsito marítimo/aéreo · 7 días",
        "region": "transito",
    },
    ETAPA_ADUANAS: {
        "orden": 7,
        "nombre": "Carga en aduanas Lima",
        "detalle": "Pendiente de pago de aranceles",
        "region": "aduanas",
    },
    ETAPA_ARANCELES: {
        "orden": 8,
        "nombre": "Aranceles pagados",
        "detalle": "2 días hasta entrega en taller",
        "region": "aduanas",
    },
    ETAPA_TALLER: {
        "orden": 9,
        "nombre": "En taller · listo para integración",
        "detalle": "Entrega final en Lima",
        "region": "taller",
    },
}

DURACION_TRANSITO_P2 = 12
DURACION_ENSAMBLADO = 13
DURACION_ENVIO = 7
DURACION_TALLER = 2
SLA_INVOICE_DIAS = 2
SLA_PRODUCCION_DIAS = 3
SLA_ARANCELES_DIAS = 1

PROVEEDORES_01 = [
    "Shenzhen Electronics Co.",
    "Foxconn Shenzhen",
    "Huaqin Technology",
]
PROVEEDORES_02 = [
    "Dongguan Assembly Ltd.",
    "Suzhou Integration Works",
    "Ningbo Board Assembly",
]

COLORES_FASE = {
    "Pago de invoice": "#B45309",
    "Producción P01": "#0F6E6B",
    "Tránsito a P02": "#0E7490",
    "Ensamblado P02": "#1D4E89",
    "Envío a Perú": "#C45C26",
    "Aduanas / aranceles": "#7C3AED",
    "Entrega a taller": "#15803D",
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


def init_db() -> None:
    """Crea el esquema y puebla 3 órdenes de prueba en etapas distintas."""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bom_code TEXT UNIQUE NOT NULL,
                descripcion TEXT NOT NULL,
                sku TEXT,
                cantidad INTEGER NOT NULL,
                proveedor_01 TEXT NOT NULL,
                proveedor_02 TEXT NOT NULL,
                invoice_nombre TEXT,
                slack_link TEXT,
                fecha_inicio TEXT NOT NULL,
                etapa TEXT NOT NULL,
                created_at TEXT NOT NULL,
                invoice_paid_at TEXT,
                produccion_at TEXT,
                transito_p2_at TEXT,
                llego_p2_at TEXT,
                ensamblado_at TEXT,
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

    # BOM-2026-001 · espera pago de invoice (SLA 1 día restante)
    inicio_001 = t0 - timedelta(days=1)
    _insert_order(
        conn,
        {
            "bom_code": "BOM-2026-001",
            "descripcion": "Mainboards control IoT v3 — lote piloto",
            "sku": "MB-IOT-V3",
            "cantidad": 250,
            "proveedor_01": "Shenzhen Electronics Co.",
            "proveedor_02": "Dongguan Assembly Ltd.",
            "invoice_nombre": "INV-SE-8841.pdf",
            "slack_link": "https://slack.com/archives/C01COMPRAS/p1726000001",
            "fecha_inicio": fmt_dt(inicio_001),
            "etapa": ETAPA_PAGO_INVOICE,
            "created_at": fmt_dt(inicio_001),
            "created_by": ROL_COMPRAS,
        },
        [
            (
                ROL_COMPRAS,
                "Orden creada",
                "Cotización INV-SE-8841.pdf adjunta",
                inicio_001,
            )
        ],
    )

    # BOM-2026-002 · ensamblado en Proveedor 02 (día 3 de 13)
    inicio_002 = t0 - timedelta(days=26)
    pago_002 = inicio_002 + timedelta(days=1)
    prod_002 = pago_002 + timedelta(hours=6)
    transito_002 = prod_002
    llego_002 = transito_002 + timedelta(days=DURACION_TRANSITO_P2)
    ensamblado_002 = llego_002 + timedelta(hours=4)
    _insert_order(
        conn,
        {
            "bom_code": "BOM-2026-002",
            "descripcion": "Mainboards gateway industrial + carcasa",
            "sku": "MB-GW-IND",
            "cantidad": 400,
            "proveedor_01": "Foxconn Shenzhen",
            "proveedor_02": "Suzhou Integration Works",
            "invoice_nombre": "INV-FX-2209.pdf",
            "slack_link": "https://slack.com/archives/C01COMPRAS/p1725000002",
            "fecha_inicio": fmt_dt(inicio_002),
            "etapa": ETAPA_ENSAMBLADO,
            "created_at": fmt_dt(inicio_002),
            "invoice_paid_at": fmt_dt(pago_002),
            "produccion_at": fmt_dt(prod_002),
            "transito_p2_at": fmt_dt(transito_002),
            "llego_p2_at": fmt_dt(llego_002),
            "ensamblado_at": fmt_dt(ensamblado_002),
            "created_by": ROL_COMPRAS,
        },
        [
            (ROL_COMPRAS, "Orden creada", "Invoice Foxconn adjunto", inicio_002),
            (ROL_FINANZAS, "Invoice pagado", "SLA 2 días cumplido", pago_002),
            (ROL_COMPRAS, "Pedido en producción", "Confirmado con Foxconn", prod_002),
            (
                "sistema",
                "Llegada a Proveedor 02",
                "Tránsito de 12 días completado",
                llego_002,
            ),
            (
                ROL_COMPRAS,
                "Ingreso a ensamblado",
                "Fase de 13 días iniciada",
                ensamblado_002,
            ),
        ],
    )

    # BOM-2026-003 · envío a Perú ya cumplió 7 días · espera marca de aduanas
    inicio_003 = t0 - timedelta(days=40)
    pago_003 = inicio_003 + timedelta(days=1)
    prod_003 = pago_003 + timedelta(hours=8)
    transito_003 = prod_003
    llego_003 = transito_003 + timedelta(days=DURACION_TRANSITO_P2)
    ensamblado_003 = llego_003 + timedelta(hours=3)
    envio_003 = ensamblado_003 + timedelta(days=DURACION_ENSAMBLADO)
    _insert_order(
        conn,
        {
            "bom_code": "BOM-2026-003",
            "descripcion": "Lote mainboards residenciales 2026-Q3",
            "sku": "MB-RES-Q3",
            "cantidad": 800,
            "proveedor_01": "Huaqin Technology",
            "proveedor_02": "Ningbo Board Assembly",
            "invoice_nombre": "INV-HQ-5510.pdf",
            "slack_link": "https://slack.com/archives/C01COMPRAS/p1724000003",
            "fecha_inicio": fmt_dt(inicio_003),
            "etapa": ETAPA_ENVIO,
            "created_at": fmt_dt(inicio_003),
            "invoice_paid_at": fmt_dt(pago_003),
            "produccion_at": fmt_dt(prod_003),
            "transito_p2_at": fmt_dt(transito_003),
            "llego_p2_at": fmt_dt(llego_003),
            "ensamblado_at": fmt_dt(ensamblado_003),
            "envio_at": fmt_dt(envio_003),
            "created_by": ROL_COMPRAS,
        },
        [
            (ROL_COMPRAS, "Orden creada", "Invoice Huaqin adjunto", inicio_003),
            (ROL_FINANZAS, "Invoice pagado", "Transferencia SWIFT registrada", pago_003),
            (ROL_COMPRAS, "Pedido en producción", "Confirmado con Huaqin", prod_003),
            (
                "sistema",
                "Llegada a Proveedor 02",
                "Tránsito de 12 días completado",
                llego_003,
            ),
            (ROL_COMPRAS, "Ingreso a ensamblado", "Producción de 13 días", ensamblado_003),
            (
                ROL_COMPRAS,
                "Mainboards en envío internacional",
                "Despacho aéreo/marítimo a aduanas Lima",
                envio_003,
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
    """Avanza etapas automáticas (tránsito 12d y entrega a taller 2d)."""
    avisos: list[str] = []
    for order in fetch_orders():
        etapa = order["etapa"]
        if etapa == ETAPA_TRANSITO_P2 and order["transito_p2_at"]:
            inicio = parse_dt(order["transito_p2_at"])
            if inicio and dias_entre(inicio, ahora) >= DURACION_TRANSITO_P2:
                update_order(
                    order["id"],
                    {
                        "etapa": ETAPA_ENSAMBLADO,
                        "llego_p2_at": order["llego_p2_at"] or fmt_dt(ahora),
                    },
                )
                add_event(
                    order["id"],
                    "sistema",
                    "Llegada a Proveedor 02",
                    "Tránsito automático de 12 días completado. Pendiente marcar ingreso a ensamblado.",
                    ahora,
                )
                avisos.append(f"{order['bom_code']}: llegó a Proveedor 02")

        if etapa == ETAPA_ARANCELES and order["aranceles_paid_at"]:
            pago = parse_dt(order["aranceles_paid_at"])
            if pago and dias_entre(pago, ahora) >= DURACION_TALLER:
                update_order(
                    order["id"],
                    {"etapa": ETAPA_TALLER, "taller_at": fmt_dt(ahora)},
                )
                add_event(
                    order["id"],
                    "sistema",
                    "Entrega en taller Lima",
                    "2 días después del pago de aranceles. Listo para integración.",
                    ahora,
                )
                avisos.append(f"{order['bom_code']}: entregado en taller")
    return avisos


def sla_invoice(order: dict, ahora: datetime) -> dict | None:
    if order["etapa"] != ETAPA_PAGO_INVOICE:
        return None
    inicio = parse_dt(order["created_at"])
    if inicio is None:
        return None
    limite = inicio + timedelta(days=SLA_INVOICE_DIAS)
    restante = dias_entre(ahora, limite)
    return {
        "tipo": "Invoice",
        "horas_restantes": restante * 24 if restante is not None else 0,
        "dias_restantes": restante,
        "vencido": ahora > limite,
        "limite": limite,
    }


def sla_produccion(order: dict, ahora: datetime) -> dict | None:
    if order["etapa"] != ETAPA_PRODUCCION:
        return None
    inicio = parse_dt(order["invoice_paid_at"]) or parse_dt(order["created_at"])
    if inicio is None:
        return None
    limite = inicio + timedelta(days=SLA_PRODUCCION_DIAS)
    restante = dias_entre(ahora, limite)
    return {
        "tipo": "Producción P01",
        "horas_restantes": restante * 24 if restante is not None else 0,
        "dias_restantes": restante,
        "vencido": ahora > limite,
        "limite": limite,
    }


def sla_aranceles(order: dict, ahora: datetime) -> dict | None:
    if order["etapa"] != ETAPA_ADUANAS:
        return None
    inicio = parse_dt(order["aduanas_at"])
    if inicio is None:
        return None
    limite = inicio + timedelta(days=SLA_ARANCELES_DIAS)
    restante = dias_entre(ahora, limite)
    return {
        "tipo": "Aranceles",
        "horas_restantes": restante * 24 if restante is not None else 0,
        "dias_restantes": restante,
        "vencido": ahora > limite,
        "limite": limite,
    }


def sla_activo(order: dict, ahora: datetime) -> dict | None:
    return sla_invoice(order, ahora) or sla_produccion(order, ahora) or sla_aranceles(order, ahora)


def conteo_fase(order: dict, ahora: datetime) -> str | None:
    etapa = order["etapa"]
    if etapa == ETAPA_TRANSITO_P2 and order["transito_p2_at"]:
        transcurridos = dias_entre(parse_dt(order["transito_p2_at"]), ahora) or 0
        resto = max(0, DURACION_TRANSITO_P2 - transcurridos)
        return f"Tránsito a P02 · día {min(int(transcurridos) + 1, DURACION_TRANSITO_P2)} de {DURACION_TRANSITO_P2} · restan {resto:.1f} d"
    if etapa == ETAPA_ENSAMBLADO and order["ensamblado_at"]:
        transcurridos = dias_entre(parse_dt(order["ensamblado_at"]), ahora) or 0
        resto = max(0, DURACION_ENSAMBLADO - transcurridos)
        return f"Ensamblado · día {min(int(transcurridos) + 1, DURACION_ENSAMBLADO)} de {DURACION_ENSAMBLADO} · restan {resto:.1f} d"
    if etapa == ETAPA_ENVIO and order["envio_at"]:
        transcurridos = dias_entre(parse_dt(order["envio_at"]), ahora) or 0
        resto = max(0, DURACION_ENVIO - transcurridos)
        estado = "llegada estimada cumplida" if resto == 0 else f"restan {resto:.1f} d"
        return f"Tránsito a Perú · día {min(int(transcurridos) + 1, DURACION_ENVIO)} de {DURACION_ENVIO} · {estado}"
    if etapa == ETAPA_ARANCELES and order["aranceles_paid_at"]:
        transcurridos = dias_entre(parse_dt(order["aranceles_paid_at"]), ahora) or 0
        resto = max(0, DURACION_TALLER - transcurridos)
        return f"Traslado a taller · restan {resto:.1f} d"
    return None


def region_de(order: dict) -> str:
    return ETAPAS[order["etapa"]]["region"]


def puede_actuar(rol: str, order: dict) -> bool:
    if rol not in ROLES_VALIDOS:
        return False
    etapa = order["etapa"]
    if rol == ROL_FINANZAS:
        return etapa in {ETAPA_PAGO_INVOICE, ETAPA_ADUANAS}
    if rol == ROL_COMPRAS:
        return etapa in {ETAPA_PRODUCCION, ETAPA_ENSAMBLADO}
    if rol == ROL_ADUANAS:
        return etapa == ETAPA_ENVIO
    return False


def avanzar_etapa(order: dict, rol: str, ahora: datetime, accion: str) -> str:
    if rol not in ROLES_VALIDOS:
        return "Rol no autorizado."

    oid = order["id"]
    etapa = order["etapa"]

    if accion == "pagar_invoice":
        if rol != ROL_FINANZAS or etapa != ETAPA_PAGO_INVOICE:
            return "Esta acción corresponde a Finanzas en la etapa de invoice."
        update_order(
            oid,
            {"etapa": ETAPA_PRODUCCION, "invoice_paid_at": fmt_dt(ahora)},
        )
        add_event(oid, rol, "Invoice pagado", "Casilla Invoice pagado registrada", ahora)
        return (
            "Invoice marcado como pagado. Compras tiene 3 días de SLA "
            "para confirmar pedido registrado / en producción."
        )

    if accion == "iniciar_produccion":
        if rol != ROL_COMPRAS or etapa != ETAPA_PRODUCCION:
            return "Esta acción corresponde a Compras al confirmar el Proveedor 01."
        update_order(
            oid,
            {
                "etapa": ETAPA_TRANSITO_P2,
                "produccion_at": fmt_dt(ahora),
                "transito_p2_at": fmt_dt(ahora),
            },
        )
        add_event(
            oid,
            rol,
            "Pedido registrado / en producción",
            "Inicia conteo de 12 días hacia Proveedor 02",
            ahora,
        )
        return "Producción confirmada. Inicia tránsito automático de 12 días a Proveedor 02."

    if accion == "ingreso_ensamblado":
        if rol != ROL_COMPRAS or etapa != ETAPA_ENSAMBLADO:
            return "Marque ingreso a ensamblado cuando la carga ya esté en Proveedor 02."
        if order["ensamblado_at"]:
            return "El ensamblado ya fue registrado."
        update_order(oid, {"ensamblado_at": fmt_dt(ahora)})
        add_event(oid, rol, "Ingreso a ensamblado", "Fase de 13 días de producción", ahora)
        return "Ingreso a ensamblado registrado (fase estimada de 13 días)."

    if accion == "enviar_peru":
        if rol != ROL_COMPRAS or etapa != ETAPA_ENSAMBLADO:
            return "El despacho lo registra Compras durante el ensamblado."
        if not order["ensamblado_at"]:
            return "Primero registre el ingreso a ensamblado."
        update_order(oid, {"etapa": ETAPA_ENVIO, "envio_at": fmt_dt(ahora)})
        add_event(
            oid,
            rol,
            "Mainboards en envío internacional",
            "Tránsito estimado de 7 días a aduanas Lima",
            ahora,
        )
        return "Despacho registrado. 7 días estimados hasta aduanas Lima."

    if accion == "marcar_aduanas":
        if rol != ROL_ADUANAS or etapa != ETAPA_ENVIO:
            return "Solo Operaciones / Aduanas puede marcar la llegada a Lima."
        update_order(oid, {"etapa": ETAPA_ADUANAS, "aduanas_at": fmt_dt(ahora)})
        add_event(
            oid,
            rol,
            "Carga en aduanas",
            "Notificación a Finanzas: pago de aranceles requerido (SLA 24 h)",
            ahora,
        )
        return "Carga en aduanas. Finanzas tiene un SLA de 24 h para aranceles."

    if accion == "pagar_aranceles":
        if rol != ROL_FINANZAS or etapa != ETAPA_ADUANAS:
            return "El pago de aranceles corresponde a Finanzas con carga en aduanas."
        update_order(
            oid,
            {"etapa": ETAPA_ARANCELES, "aranceles_paid_at": fmt_dt(ahora)},
        )
        add_event(
            oid,
            rol,
            "Aranceles pagados",
            "En 2 días la carga pasa automáticamente a taller Lima",
            ahora,
        )
        return "Aranceles pagados. Entrega automática a taller en 2 días."

    return "Acción no reconocida."


# ---------------------------------------------------------------------------
# Métricas y gráficos
# ---------------------------------------------------------------------------


def orders_frame(orders: list[dict], ahora: datetime) -> pd.DataFrame:
    rows = []
    for order in orders:
        sla = sla_activo(order, ahora)
        etapa_meta = ETAPAS[order["etapa"]]
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
                "Progreso": etapa_meta["orden"] / TOTAL_ETAPAS,
                "Fecha inicio": parse_dt(order["fecha_inicio"]),
                "SLA": (
                    "Vencido"
                    if sla and sla["vencido"]
                    else (
                        f"{sla['dias_restantes']:.1f} d"
                        if sla
                        else "—"
                    )
                ),
                "Invoice / Slack": order["invoice_nombre"] or order["slack_link"] or "—",
            }
        )
    return pd.DataFrame(rows)


def kpis(orders: list[dict], ahora: datetime) -> dict[str, int]:
    return {
        "total": len(orders),
        "china": sum(1 for o in orders if region_de(o) == "china"),
        "transito": sum(1 for o in orders if region_de(o) == "transito"),
        "aduanas": sum(1 for o in orders if region_de(o) == "aduanas"),
        "taller": sum(1 for o in orders if region_de(o) == "taller"),
        "sla": sum(1 for o in orders if (s := sla_activo(o, ahora)) and s["vencido"]),
    }


def build_gantt(orders: list[dict], ahora: datetime) -> pd.DataFrame:
    """Ciclo de vida planificado / real de cada BOM para el timeline."""
    plan = [
        ("Pago de invoice", "created_at", "invoice_paid_at", SLA_INVOICE_DIAS, ETAPA_PAGO_INVOICE),
        ("Producción P01", "invoice_paid_at", "produccion_at", SLA_PRODUCCION_DIAS, ETAPA_PRODUCCION),
        ("Tránsito a P02", "transito_p2_at", "llego_p2_at", DURACION_TRANSITO_P2, ETAPA_TRANSITO_P2),
        ("Ensamblado P02", "ensamblado_at", "envio_at", DURACION_ENSAMBLADO, ETAPA_ENSAMBLADO),
        ("Envío a Perú", "envio_at", "aduanas_at", DURACION_ENVIO, ETAPA_ENVIO),
        ("Aduanas / aranceles", "aduanas_at", "aranceles_paid_at", SLA_ARANCELES_DIAS, ETAPA_ADUANAS),
        ("Entrega a taller", "aranceles_paid_at", "taller_at", DURACION_TALLER, ETAPA_ARANCELES),
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
                # Conservar la duración SLA/estimada (p. ej. 3 días de P01)
                # aunque la confirmación aún no ocurra. Si ya venció, alargar hasta hoy.
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
        title="Ciclo de vida China → Lima",
    )
    fig.update_yaxes(autorange="reversed", title="")
    fig.update_xaxes(title="Fecha operativa")
    fig.update_traces(marker_line_width=0)
    fig.add_vline(
        x=ahora,
        line_width=1.5,
        line_dash="dot",
        line_color="#B45309",
        annotation_text="Hoy",
        annotation_position="top",
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
    color_map = {
        ETAPAS[k]["nombre"]: color
        for k, color in zip(
            ETAPAS,
            ["#B45309", "#0F6E6B", "#0E7490", "#1D4E89", "#C45C26", "#7C3AED", "#A78BFA", "#15803D"],
        )
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
    regiones = {"china": "En China", "transito": "En tránsito a Perú", "aduanas": "En aduanas", "taller": "En taller"}
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
        color_discrete_sequence=["#0F6E6B", "#C45C26", "#7C3AED", "#15803D"],
        title="Inventario de órdenes por ubicación",
    )
    fig.update_layout(showlegend=False)
    fig.update_yaxes(dtick=1)
    return style_plotly(fig)


# ---------------------------------------------------------------------------
# Acciones de UI
# ---------------------------------------------------------------------------


def guardar_pdf(uploaded, bom_code: str) -> str | None:
    if uploaded is None:
        return None
    safe_name = f"{bom_code}_{uploaded.name}".replace(" ", "_")
    destino = UPLOAD_DIR / safe_name
    destino.write_bytes(uploaded.getbuffer())
    return safe_name


def crear_orden(form: dict, ahora: datetime) -> str:
    bom = next_bom_code()
    invoice_nombre = guardar_pdf(form["pdf"], bom)
    payload = {
        "bom_code": bom,
        "descripcion": form["descripcion"].strip(),
        "sku": form["sku"].strip() or None,
        "cantidad": int(form["cantidad"]),
        "proveedor_01": form["proveedor_01"],
        "proveedor_02": form["proveedor_02"],
        "invoice_nombre": invoice_nombre,
        "slack_link": form["slack_link"].strip() or None,
        "fecha_inicio": fmt_dt(datetime.combine(form["fecha_inicio"], ahora.time())),
        "etapa": ETAPA_PAGO_INVOICE,
        "created_at": fmt_dt(ahora),
        "created_by": ROL_COMPRAS,
    }
    with get_conn() as conn:
        _insert_order(
            conn,
            payload,
            [
                (
                    ROL_COMPRAS,
                    "Orden creada",
                    f"Invoice: {invoice_nombre or 'sin PDF'} · Slack: {payload['slack_link'] or '—'}",
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
            "Usa el avance de día para disparar el tránsito de 12 días "
            "y la entrega automática al taller (2 días post-aranceles)."
        )

        with st.expander("Datos de demostración", icon=":material/database:"):
            st.caption("Tres BOM de prueba en invoice, ensamblado y envío a Perú.")
            if st.button("Restablecer demo", icon=":material/restart_alt:"):
                reset_demo()
                st.toast("Demo restablecida", icon=":material/check:")
                st.rerun()

        st.caption("Metta Dashboard · MVP trazabilidad v0.1")
    return rol


def render_kpis(metrics: dict[str, int]) -> None:
    with st.container(horizontal=True):
        st.metric("Total de órdenes", metrics["total"], border=True)
        st.metric("Órdenes en China", metrics["china"], border=True)
        st.metric("En tránsito a Perú", metrics["transito"], border=True)
    with st.container(horizontal=True):
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
                "Invoice / Slack": st.column_config.TextColumn("Invoice / Slack"),
            },
        )


def _sla_markdown(sla: dict) -> None:
    if sla["vencido"]:
        demora = abs(sla["dias_restantes"])
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
        meta1.markdown(f"**Proveedor 01**  \n{order['proveedor_01']}")
        meta2.markdown(f"**Proveedor 02**  \n{order['proveedor_02']}")
        meta3.markdown(f"**Cantidad**  \n{order['cantidad']} · {order['sku'] or 's/SKU'}")

        if order["invoice_nombre"] or order["slack_link"]:
            refs = []
            if order["invoice_nombre"]:
                refs.append(f"PDF: `{order['invoice_nombre']}`")
            if order["slack_link"]:
                refs.append(f"[Hilo Slack]({order['slack_link']})")
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

    if etapa == ETAPA_TALLER:
        st.success("Carga en taller Lima · lista para integración.", icon=":material/factory:")
        return

    if etapa == ETAPA_ARANCELES:
        st.caption("Esperando el traslado automático de 2 días al taller. Avance el reloj operativo para simularlo.")
        return

    if etapa == ETAPA_PAGO_INVOICE:
        habilitado = rol == ROL_FINANZAS
        if not habilitado:
            st.caption("Acción bloqueada · requiere Usuario 02 (Finanzas).")
        checked = st.checkbox(
            "Invoice pagado",
            key=f"chk_invoice_{oid}",
            disabled=not habilitado,
        )
        if st.button(
            "Registrar pago de invoice",
            key=f"btn_invoice_{oid}",
            icon=":material/payments:",
            type="primary",
            disabled=not (habilitado and checked),
        ):
            mensaje = avanzar_etapa(order, rol, ahora, "pagar_invoice")
            st.toast(mensaje, icon=":material/check:")
            st.rerun()
        return

    if etapa == ETAPA_PRODUCCION:
        habilitado = rol == ROL_COMPRAS
        if not habilitado:
            st.caption(
                "Acción bloqueada · requiere Usuario 01 (Compras / logística) · SLA 3 días."
            )
        if st.button(
            "Pedido registrado / en producción",
            key=f"btn_prod_{oid}",
            icon=":material/precision_manufacturing:",
            type="primary",
            disabled=not habilitado,
        ):
            mensaje = avanzar_etapa(order, rol, ahora, "iniciar_produccion")
            st.toast(mensaje, icon=":material/check:")
            st.rerun()
        return

    if etapa == ETAPA_TRANSITO_P2:
        st.caption("Etapa automática. El sistema marcará la llegada a Proveedor 02 a los 12 días.")
        return

    if etapa == ETAPA_ENSAMBLADO:
        habilitado = rol == ROL_COMPRAS
        if not habilitado:
            st.caption("Acción bloqueada · requiere Usuario 01 (Compras / logística).")
        if not order["ensamblado_at"]:
            if st.button(
                "Ingreso a ensamblado",
                key=f"btn_ens_{oid}",
                icon=":material/build:",
                type="primary",
                disabled=not habilitado,
            ):
                mensaje = avanzar_etapa(order, rol, ahora, "ingreso_ensamblado")
                st.toast(mensaje, icon=":material/check:")
                st.rerun()
        else:
            if st.button(
                "Mainboards en envío internacional",
                key=f"btn_ship_{oid}",
                icon=":material/flight_takeoff:",
                type="primary",
                disabled=not habilitado,
            ):
                mensaje = avanzar_etapa(order, rol, ahora, "enviar_peru")
                st.toast(mensaje, icon=":material/check:")
                st.rerun()
        return

    if etapa == ETAPA_ENVIO:
        habilitado = rol == ROL_ADUANAS
        if not habilitado:
            st.caption("Acción bloqueada · requiere Usuario 03 (Operaciones / aduanas Lima).")
        if st.button(
            "Carga en aduanas",
            key=f"btn_adu_{oid}",
            icon=":material/warehouse:",
            type="primary",
            disabled=not habilitado,
        ):
            mensaje = avanzar_etapa(order, rol, ahora, "marcar_aduanas")
            st.success(mensaje, icon=":material/campaign:")
            st.toast(mensaje, icon=":material/campaign:")
            st.rerun()
        return

    if etapa == ETAPA_ADUANAS:
        habilitado = rol == ROL_FINANZAS
        if not habilitado:
            st.caption("Acción bloqueada · requiere Usuario 02 (Finanzas) · SLA 24 h.")
        checked = st.checkbox(
            "Aranceles pagados",
            key=f"chk_tax_{oid}",
            disabled=not habilitado,
        )
        if st.button(
            "Registrar pago de aranceles",
            key=f"btn_tax_{oid}",
            icon=":material/account_balance:",
            type="primary",
            disabled=not (habilitado and checked),
        ):
            mensaje = avanzar_etapa(order, rol, ahora, "pagar_aranceles")
            st.toast(mensaje, icon=":material/check:")
            st.rerun()


def render_gestion(orders: list[dict], rol: str, ahora: datetime) -> None:
    activas = [o for o in orders if o["etapa"] != ETAPA_TALLER]
    cerradas = [o for o in orders if o["etapa"] == ETAPA_TALLER]

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
    if rol != ROL_COMPRAS:
        st.warning(
            "Solo el Usuario 01 (Compras y logística internacional) puede registrar un nuevo BOM.",
            icon=":material/lock:",
        )
        st.caption("Cambia el rol en la barra lateral para habilitar el formulario.")
        return

    st.subheader("Nueva orden de compra")
    st.caption("Adjunta la cotización/invoice del Proveedor 01 o un enlace al hilo de Slack.")

    with st.form("nueva_orden", border=True):
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
            proveedor_01 = st.selectbox("Proveedor 01 (origen China)", PROVEEDORES_01)
        with p2:
            proveedor_02 = st.selectbox("Proveedor 02 (ensamblado)", PROVEEDORES_02)

        pdf = st.file_uploader("Cotización / invoice (PDF)", type=["pdf"])
        slack_link = st.text_input(
            "Enlace de Slack (opcional)",
            placeholder="https://slack.com/archives/...",
        )

        enviado = st.form_submit_button(
            "Crear orden",
            icon=":material/add_box:",
            type="primary",
        )

    if enviado:
        if not descripcion.strip():
            st.error("La descripción es obligatoria.", icon=":material/error:")
            return
        if pdf is None and not slack_link.strip():
            st.error(
                "Adjunta un PDF o un enlace de Slack para trazabilidad del invoice.",
                icon=":material/attach_file:",
            )
            return
        bom = crear_orden(
            {
                "descripcion": descripcion,
                "sku": sku,
                "cantidad": cantidad,
                "fecha_inicio": fecha_inicio,
                "proveedor_01": proveedor_01,
                "proveedor_02": proveedor_02,
                "pdf": pdf,
                "slack_link": slack_link,
            },
            ahora,
        )
        st.success(
            f"Orden **{bom}** creada. Finanzas tiene 2 días de SLA para pagar el invoice.",
            icon=":material/check_circle:",
        )
        st.balloons()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

st.session_state.setdefault("sim_days", 0)
st.session_state.setdefault("rol_activo", ROL_COMPRAS)

init_db()
rol_activo = render_sidebar()
ahora = now_operativo()
avisos = apply_automatic_transitions(ahora)
orders = fetch_orders()

st.title("Trazabilidad de producción")
st.caption(
    "Control logístico de insumos desde China hasta Lima · "
    f"operando como **{ROLES[rol_activo]['label']}** · "
    f"{ahora.strftime('%d %b %Y %H:%M')}"
)

for aviso in avisos:
    st.toast(aviso, icon=":material/sync:")

dashboard_tab, gestion_tab, alta_tab = st.tabs(
    [
        ":material/space_dashboard: Dashboard principal",
        ":material/tune: Gestión y control",
        ":material/add_box: Registro de nueva orden",
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
