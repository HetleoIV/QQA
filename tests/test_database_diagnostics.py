import json
import secrets

import pytest

from app import create_app
from app.extensions import db
from app import database_diagnostics


@pytest.fixture
def diagnostic_app(tmp_path, monkeypatch):
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("DATABASE_DIAGNOSTIC_TOKEN", token)
    monkeypatch.setenv("DATABASE_URL", "configured-test-only")
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": secrets.token_urlsafe(32),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'diagnostic.sqlite3').as_posix()}",
        }
    )
    return app, token


class FakeResult:
    def scalar_one(self):
        return 1

    def scalars(self):
        return self

    def all(self):
        return ["934475c0b3e8"]


class FakeTransaction:
    is_active = True

    def __init__(self):
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True
        self.is_active = False


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.transaction = FakeTransaction()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def begin(self):
        return self.transaction

    def exec_driver_sql(self, statement):
        self.statements.append(statement)
        return FakeResult()

    def execute(self, statement):
        self.statements.append(str(statement))
        return FakeResult()


class FakeInspector:
    def get_table_names(self, schema):
        assert schema == "public"
        return [
            "administrators",
            "alembic_version",
            "appointments",
            "availability_slots",
            "appointment_status_history",
            "email_notifications",
            "professional_services",
            "professionals",
            "services",
        ]


def configure_fake_postgres_connection(app, monkeypatch, connection):
    with app.app_context():
        engine = db.engine
        monkeypatch.setattr(engine.dialect, "name", "postgresql")
        monkeypatch.setattr(engine.dialect, "driver", "psycopg")
        monkeypatch.setattr(engine, "connect", lambda: connection)
    monkeypatch.setattr(database_diagnostics, "inspect", lambda _connection: FakeInspector())


def test_diagnostico_correcto_devuelve_solo_metadatos_y_revierte_transaccion(
    diagnostic_app, monkeypatch
):
    app, token = diagnostic_app
    connection = FakeConnection()
    configure_fake_postgres_connection(app, monkeypatch, connection)

    response = app.test_client().post(
        "/_ops/database-check",
        headers={database_diagnostics.DIAGNOSTIC_TOKEN_HEADER: token},
    )

    payload = json.loads(response.get_data(as_text=True))
    assert response.status_code == 200
    assert payload["database_url_configured"] is True
    assert payload["sqlalchemy"] == {"dialect": "postgresql", "driver": "psycopg"}
    assert payload["connection"] == {"ok": True, "select_1": True}
    assert payload["schema"]["alembic_version_table_exists"] is True
    assert payload["schema"]["alembic_revisions"] == ["934475c0b3e8"]
    assert "administrators" in payload["schema"]["expected_tables_present"]
    assert payload["schema"]["expected_tables_missing"] == []
    assert connection.statements[0] == "SET TRANSACTION READ ONLY"
    assert connection.statements[1] == "SELECT 1"
    assert connection.transaction.rolled_back is True
    assert connection.closed is True
    assert response.headers["Cache-Control"] == "no-store"
    assert token not in response.get_data(as_text=True)
    assert "configured-test-only" not in response.get_data(as_text=True)


def test_token_incorrecto_no_intenta_conectar(diagnostic_app, monkeypatch):
    app, _token = diagnostic_app
    with app.app_context():
        monkeypatch.setattr(db.engine, "connect", lambda: pytest.fail("No debe conectar"))

    response = app.test_client().post(
        "/_ops/database-check",
        headers={database_diagnostics.DIAGNOSTIC_TOKEN_HEADER: secrets.token_urlsafe(32)},
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "No autorizado."}


def test_sin_token_de_diagnostico_la_ruta_no_se_registra(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_DIAGNOSTIC_TOKEN", raising=False)
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": secrets.token_urlsafe(32),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'disabled.sqlite3').as_posix()}",
        }
    )

    response = app.test_client().post("/_ops/database-check")

    assert response.status_code == 404


def test_fallo_de_conexion_devuelve_error_sanitizado(diagnostic_app, monkeypatch):
    app, token = diagnostic_app
    sensitive_error = "private-dsn-user-password-host"
    with app.app_context():
        engine = db.engine
        monkeypatch.setattr(engine.dialect, "name", "postgresql")
        monkeypatch.setattr(engine.dialect, "driver", "psycopg")
        monkeypatch.setattr(
            engine,
            "connect",
            lambda: (_ for _ in ()).throw(RuntimeError(sensitive_error)),
        )

    response = app.test_client().post(
        "/_ops/database-check",
        headers={database_diagnostics.DIAGNOSTIC_TOKEN_HEADER: token},
    )

    payload = response.get_json()
    assert response.status_code == 503
    assert payload["status"] == "database_unavailable_or_query_failed"
    assert payload["connection"] == {"ok": False, "select_1": False}
    assert sensitive_error not in response.get_data(as_text=True)
    assert token not in response.get_data(as_text=True)


def test_endpoint_es_solo_post_y_no_guarda_cache(diagnostic_app):
    app, _token = diagnostic_app

    response = app.test_client().get("/_ops/database-check")

    assert response.status_code == 405


def test_motor_sqlite_no_se_consulta_como_si_fuera_produccion_postgresql(
    diagnostic_app, monkeypatch
):
    app, token = diagnostic_app
    with app.app_context():
        monkeypatch.setattr(db.engine, "connect", lambda: pytest.fail("No debe conectar"))

    response = app.test_client().post(
        "/_ops/database-check",
        headers={database_diagnostics.DIAGNOSTIC_TOKEN_HEADER: token},
    )

    payload = response.get_json()
    assert response.status_code == 503
    assert payload["sqlalchemy"]["dialect"] == "sqlite"
    assert payload["status"] == "unsupported_database_driver"
    assert payload["connection"]["ok"] is False
