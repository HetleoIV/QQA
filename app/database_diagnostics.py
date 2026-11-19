"""Endpoint temporal autenticado para diagnosticar PostgreSQL sin escrituras."""

import hmac
import os

from flask import Blueprint, jsonify, make_response, request
from sqlalchemy import inspect, text

from .extensions import db


database_diagnostics_bp = Blueprint(
    "database_diagnostics", __name__, url_prefix="/_ops"
)

EXPECTED_TABLES = {
    "administrators",
    "services",
    "professionals",
    "professional_services",
    "availability_slots",
    "appointments",
    "email_notifications",
    "appointment_status_history",
}
DIAGNOSTIC_TOKEN_HEADER = "X-QAA-Diagnostic-Token"


def _response(payload: dict, status: int = 200):
    response = make_response(jsonify(payload), status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _token_is_valid() -> bool:
    expected = os.environ.get("DATABASE_DIAGNOSTIC_TOKEN", "")
    supplied = request.headers.get(DIAGNOSTIC_TOKEN_HEADER, "")
    if not expected or not supplied:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))


@database_diagnostics_bp.post("/database-check")
def database_check():
    """Devuelve solo metadatos no sensibles de una conexión PostgreSQL."""
    if not os.environ.get("DATABASE_DIAGNOSTIC_TOKEN"):
        return _response({"error": "Diagnóstico deshabilitado."}, 404)
    if not _token_is_valid():
        return _response({"error": "No autorizado."}, 401)

    database_url_configured = bool(os.environ.get("DATABASE_URL", "").strip())
    result = {
        "database_url_configured": database_url_configured,
        "sqlalchemy": {"dialect": None, "driver": None},
        "endpoint_looks_like_neon": None,
        "connection": {"ok": False, "select_1": False},
        "schema": {
            "tables": None,
            "expected_tables_present": None,
            "expected_tables_missing": None,
            "alembic_version_table_exists": None,
            "alembic_revisions": None,
        },
    }

    try:
        engine = db.engine
        dialect_name = engine.dialect.name
        driver_name = engine.dialect.driver
        host = engine.url.host or ""
    except Exception:
        result["status"] = "database_unavailable_or_query_failed"
        return _response(result, 503)

    result["sqlalchemy"] = {"dialect": dialect_name, "driver": driver_name}
    result["endpoint_looks_like_neon"] = host.casefold().endswith(".neon.tech")

    if dialect_name != "postgresql" or driver_name != "psycopg":
        result["status"] = "unsupported_database_driver"
        return _response(result, 503)

    transaction = None
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                # Debe ser la primera sentencia de la transacción PostgreSQL.
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                select_1 = connection.exec_driver_sql("SELECT 1").scalar_one()
                tables = set(inspect(connection).get_table_names(schema="public"))

                result["connection"] = {"ok": True, "select_1": select_1 == 1}
                result["schema"] = {
                    "tables": sorted(tables),
                    "expected_tables_present": sorted(EXPECTED_TABLES & tables),
                    "expected_tables_missing": sorted(EXPECTED_TABLES - tables),
                    "alembic_version_table_exists": "alembic_version" in tables,
                    "alembic_revisions": None,
                }
                if "alembic_version" in tables:
                    revisions = connection.execute(
                        text("SELECT version_num FROM public.alembic_version")
                    ).scalars().all()
                    result["schema"]["alembic_revisions"] = revisions
                result["status"] = "ok"
            finally:
                if transaction is not None and transaction.is_active:
                    transaction.rollback()
    except Exception:
        # No se incluye ni registra la excepción: los drivers pueden adjuntar
        # detalles de conexión que contienen información sensible.
        result["status"] = "database_unavailable_or_query_failed"
        return _response(result, 503)

    return _response(result)
