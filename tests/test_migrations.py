import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
    "administrators", "professionals", "services", "professional_services",
    "availability_slots", "appointments", "email_notifications",
    "appointment_status_history",
}


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
