import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import event

from app import create_app
from app.extensions import db


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
    "administrators", "professionals", "services", "professional_services",
    "availability_slots", "appointments", "email_notifications",
    "appointment_status_history",
}


def _create_legacy_database(tmp_path, filename):
    """Crea la revisión inicial con una cita asignada y relaciones ficticias."""
    database_file = tmp_path / filename
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_file.as_posix()}",
    })
    result = app.test_cli_runner().invoke(
        args=["db", "upgrade", "934475c0b3e8"]
    )
    assert result.exit_code == 0, result.output

    with sqlite3.connect(database_file) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO professionals (id, full_name) VALUES (1, ?)",
            ("Profesional ficticio",),
        )
        connection.execute(
            "INSERT INTO professional_services (professional_id, service_id) VALUES (1, 1)"
        )
        connection.execute(
            "INSERT INTO availability_slots "
            "(id, professional_id, starts_at, ends_at) VALUES (1, 1, ?, ?)",
            ("2035-01-01 09:00:00.000000", "2035-01-01 10:00:00.000000"),
        )
        connection.execute(
            "INSERT INTO appointments "
            "(id, availability_slot_id, professional_id, service_id, modality, "
            "contact_name, contact_email, public_ticket_hash, status, hold_expires_at) "
            "VALUES (1, 1, 1, 1, 'virtual', 'Contacto ficticio', ?, ?, 'pending', ?)",
            (
                "contact@example.test", "a" * 64,
                "2035-01-01 08:15:00.000000",
            ),
        )
        connection.execute(
            "INSERT INTO appointment_status_history "
            "(id, appointment_id, new_status, change_source) "
            "VALUES (1, 1, 'pending', 'system')"
        )
        connection.execute(
            "INSERT INTO email_notifications "
            "(id, appointment_id, notification_type, recipient_email, idempotency_key) "
            "VALUES (1, 1, 'appointment_pending', 'contact@example.test', 'pending-1')"
        )
    return app, database_file


def test_migracion_inicial_crea_esquema_sqlite_y_triggers_en_base_vacia(tmp_path):
    database_file = tmp_path / "qaa-empty.sqlite3"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database_file.as_posix()}"
    environment["FLASK_DEBUG"] = "false"

    result = subprocess.run(
        [sys.executable, "-m", "flask", "--app", "run.py", "db", "upgrade"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(database_file) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert EXPECTED_TABLES <= tables
        assert "alembic_version" in tables
        assert connection.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 5
        assert {
            row[0] for row in connection.execute("SELECT slug FROM services")
        } == {
            "psicologia-clinica", "psicologia-forense", "psicologia-organizacional",
            "asesoria-legal", "orientacion-nutricional",
        }

        appointments_fks = connection.execute(
            "PRAGMA foreign_key_list('appointments')"
        ).fetchall()
        assert {row[2] for row in appointments_fks} >= {
            "availability_slots", "professional_services", "professionals", "services"
        }
        composite_slot_fk = {
            (row[3], row[4])
            for row in appointments_fks
            if row[2] == "availability_slots"
        }
        assert composite_slot_fk == {
            ("availability_slot_id", "id"),
            ("professional_id", "professional_id"),
        }

        appointment_indexes = connection.execute(
            "PRAGMA index_list('appointments')"
        ).fetchall()
        assert "uq_appointments_active_slot" in {row[1] for row in appointment_indexes}
        unique_column_sets = {
            tuple(info[2] for info in connection.execute(f"PRAGMA index_info('{row[1]}')"))
            for row in appointment_indexes
            if row[2]
        }
        assert ("public_ticket_hash",) in unique_column_sets

        history_fks = {
            row[2]
            for row in connection.execute(
                "PRAGMA foreign_key_list('appointment_status_history')"
            )
        }
        assert {"appointments", "administrators"} <= history_fks
        email_fks = {
            row[2]
            for row in connection.execute("PRAGMA foreign_key_list('email_notifications')")
        }
        assert email_fks == {"appointments"}

        trigger_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert trigger_names == {
            "trg_availability_slots_validate_insert",
            "trg_availability_slots_validate_update",
        }

        connection.execute(
            "INSERT INTO professionals (full_name) VALUES (?)", ("Profesional ficticio",)
        )
        professional_id = connection.execute(
            "SELECT id FROM professionals"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO availability_slots (professional_id, starts_at, ends_at) VALUES (?, ?, ?)",
            (professional_id, "2030-01-01 09:00:00.000000", "2030-01-01 10:00:00.000000"),
        )
        connection.execute(
            "INSERT INTO availability_slots (professional_id, starts_at, ends_at) VALUES (?, ?, ?)",
            (professional_id, "2030-01-01 10:00:00.000000", "2030-01-01 11:00:00.000000"),
        )
        with pytest.raises(sqlite3.IntegrityError, match="overlaps"):
            connection.execute(
                "INSERT INTO availability_slots (professional_id, starts_at, ends_at) VALUES (?, ?, ?)",
                (professional_id, "2030-01-01 09:30:00.000000", "2030-01-01 10:30:00.000000"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="overlaps"):
            connection.execute(
                "UPDATE availability_slots SET starts_at = ?, ends_at = ? WHERE starts_at = ?",
                (
                    "2030-01-01 09:30:00.000000", "2030-01-01 10:30:00.000000",
                    "2030-01-01 10:00:00.000000",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="60 minutes"):
            connection.execute(
                "INSERT INTO availability_slots (professional_id, starts_at, ends_at) VALUES (?, ?, ?)",
                (professional_id, "2030-01-01 10:00:00.000000", "2030-01-01 10:45:00.000000"),
            )


def test_migracion_permite_solicitudes_no_asignadas_y_preserva_datos_relacionados(tmp_path):
    database_file = tmp_path / "qaa-upgrade-with-appointments.sqlite3"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database_file.as_posix()}"
    environment["FLASK_DEBUG"] = "false"

    initial_upgrade = subprocess.run(
        [
            sys.executable, "-m", "flask", "--app", "run.py", "db", "upgrade",
            "934475c0b3e8",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert initial_upgrade.returncode == 0, initial_upgrade.stdout + initial_upgrade.stderr

    with sqlite3.connect(database_file) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO administrators (id, email, password_hash) VALUES (1, ?, ?)",
            ("admin@example.test", "hash-ficticio"),
        )
        connection.execute(
            "INSERT INTO professionals (id, full_name) VALUES (1, ?)",
            ("Profesional ficticio",),
        )
        connection.execute(
            "INSERT INTO professional_services (professional_id, service_id) VALUES (1, 1)"
        )
        connection.execute(
            "INSERT INTO availability_slots "
            "(id, professional_id, starts_at, ends_at) VALUES (1, 1, ?, ?)",
            ("2035-01-01 09:00:00.000000", "2035-01-01 10:00:00.000000"),
        )
        connection.execute(
            "INSERT INTO appointments "
            "(id, availability_slot_id, professional_id, service_id, modality, "
            "contact_name, contact_email, public_ticket_hash, status, hold_expires_at) "
            "VALUES (1, 1, 1, 1, 'virtual', 'Contacto ficticio', ?, ?, 'pending', ?)",
            (
                "contact@example.test", "a" * 64,
                "2035-01-01 08:15:00.000000",
            ),
        )
        connection.execute(
            "INSERT INTO appointment_status_history "
            "(id, appointment_id, new_status, changed_by_administrator_id, change_source) "
            "VALUES (1, 1, 'pending', 1, 'system')"
        )
        connection.execute(
            "INSERT INTO email_notifications "
            "(id, appointment_id, notification_type, recipient_email, idempotency_key) "
            "VALUES (1, 1, 'appointment_pending', 'admin@example.test', 'appointment-1-pending')"
        )

    incremental_upgrade = subprocess.run(
        [sys.executable, "-m", "flask", "--app", "run.py", "db", "upgrade"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert incremental_upgrade.returncode == 0, (
        incremental_upgrade.stdout + incremental_upgrade.stderr
    )

    with sqlite3.connect(database_file) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        columns = {
            row[1]: row[3] for row in connection.execute("PRAGMA table_info('appointments')")
        }
        assert columns["service_id"] == 1
        assert columns["professional_id"] == 0
        assert columns["availability_slot_id"] == 0
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            "c2a8e6149f30"
        )
        assert connection.execute(
            "SELECT availability_slot_id, professional_id, service_id, contact_email, "
            "public_ticket_hash, status, hold_expires_at FROM appointments WHERE id=1"
        ).fetchone() == (
            1, 1, 1, "contact@example.test", "a" * 64, "pending",
            "2035-01-01 08:15:00.000000",
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM appointment_status_history WHERE appointment_id=1"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM email_notifications WHERE appointment_id=1"
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        # Las solicitudes no asignadas no ocupan el índice de horarios activos.
        for ticket_hash in ("b" * 64, "c" * 64):
            connection.execute(
                "INSERT INTO appointments "
                "(availability_slot_id, professional_id, service_id, modality, "
                "contact_name, contact_email, public_ticket_hash, status) "
                "VALUES (NULL, NULL, 1, 'virtual', 'Solicitud ficticia', ?, ?, 'pending')",
                ("request@example.test", ticket_hash),
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO appointments "
                "(availability_slot_id, professional_id, service_id, modality, "
                "contact_name, contact_email, public_ticket_hash, status) "
                "VALUES (NULL, 1, 1, 'virtual', 'Asignación parcial', ?, ?, 'pending')",
                ("partial@example.test", "d" * 64),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO appointments "
                "(availability_slot_id, professional_id, service_id, modality, "
                "contact_name, contact_email, public_ticket_hash, status) "
                "VALUES (NULL, NULL, 1, 'virtual', 'Confirmada sin asignar', ?, ?, 'confirmed')",
                ("confirmed@example.test", "e" * 64),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO appointments "
                "(availability_slot_id, professional_id, service_id, modality, "
                "contact_name, contact_email, public_ticket_hash, status) "
                "VALUES (1, 1, 1, 'virtual', 'Reserva simultánea', ?, ?, 'confirmed')",
                ("conflict@example.test", "g" * 64),
            )

    downgrade = subprocess.run(
        [
            sys.executable, "-m", "flask", "--app", "run.py", "db", "downgrade",
            "934475c0b3e8",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert downgrade.returncode != 0
    assert "existen solicitudes sin profesional u horario" in (
        downgrade.stdout + downgrade.stderr
    )
    with sqlite3.connect(database_file) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            "c2a8e6149f30"
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM appointments WHERE professional_id IS NULL"
        ).fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_fallo_intermedio_revierte_ddl_y_permite_reintentar(tmp_path):
    app, database_file = _create_legacy_database(
        tmp_path, "qaa-interrupted-migration.sqlite3"
    )
    failure_injected = False

    def fail_during_batch_insert(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        nonlocal failure_injected
        if (
            not failure_injected
            and "INSERT INTO _alembic_tmp_appointments" in statement
        ):
            failure_injected = True
            raise RuntimeError("fallo intermedio inducido por la prueba")

    with app.app_context():
        engine = db.engine
        event.listen(engine, "before_cursor_execute", fail_during_batch_insert)
        try:
            failed_upgrade = app.test_cli_runner().invoke(args=["db", "upgrade"])
        finally:
            event.remove(engine, "before_cursor_execute", fail_during_batch_insert)

        assert failure_injected
        assert failed_upgrade.exit_code != 0
        assert "fallo intermedio inducido" in (
            failed_upgrade.output + str(failed_upgrade.exception)
        )

        # La conexión vuelve al pool con la aplicación de claves foráneas activa.
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "934475c0b3e8"
            columns = {
                row[1]: row[3]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info('appointments')"
                ).fetchall()
            }
            assert columns["professional_id"] == 1
            assert columns["availability_slot_id"] == 1
            tables = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "_alembic_tmp_appointments" not in tables
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            assert connection.exec_driver_sql(
                "SELECT contact_email FROM appointments WHERE id=1"
            ).scalar_one() == "contact@example.test"

        retry = app.test_cli_runner().invoke(args=["db", "upgrade"])
        assert retry.exit_code == 0, retry.output

    with sqlite3.connect(database_file) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == "c2a8e6149f30"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_foreign_key_check_fallido_revierte_antes_de_confirmar(tmp_path):
    app, database_file = _create_legacy_database(
        tmp_path, "qaa-failed-foreign-key-check.sqlite3"
    )
    with sqlite3.connect(database_file) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO appointment_status_history "
            "(id, appointment_id, new_status, change_source) "
            "VALUES (99, 999, 'pending', 'system')"
        )

    with app.app_context():
        failed_upgrade = app.test_cli_runner().invoke(args=["db", "upgrade"])
        assert failed_upgrade.exit_code != 0
        assert "referencias foráneas inválidas" in (
            failed_upgrade.output + str(failed_upgrade.exception)
        )

        with db.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "934475c0b3e8"
            columns = {
                row[1]: row[3]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info('appointments')"
                ).fetchall()
            }
            assert columns["professional_id"] == 1
            assert columns["availability_slot_id"] == 1


def test_downgrade_exitoso_conserva_cita_y_relaciones(tmp_path):
    app, database_file = _create_legacy_database(
        tmp_path, "qaa-successful-downgrade.sqlite3"
    )

    with app.app_context():
        upgrade = app.test_cli_runner().invoke(args=["db", "upgrade"])
        assert upgrade.exit_code == 0, upgrade.output
        downgrade = app.test_cli_runner().invoke(
            args=["db", "downgrade", "934475c0b3e8"]
        )
        assert downgrade.exit_code == 0, downgrade.output

    with sqlite3.connect(database_file) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == "934475c0b3e8"
        columns = {
            row[1]: row[3]
            for row in connection.execute("PRAGMA table_info('appointments')")
        }
        assert columns["professional_id"] == 1
        assert columns["availability_slot_id"] == 1
        assert connection.execute(
            "SELECT availability_slot_id, professional_id, service_id, contact_email, "
            "public_ticket_hash FROM appointments WHERE id=1"
        ).fetchone() == (1, 1, 1, "contact@example.test", "a" * 64)
        assert connection.execute(
            "SELECT appointment_id FROM appointment_status_history WHERE id=1"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT appointment_id FROM email_notifications WHERE id=1"
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        index_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='uq_appointments_active_slot'"
        ).fetchone()[0]
        assert "WHERE status IN ('pending', 'confirmed')" in index_sql
