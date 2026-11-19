from datetime import date, time, timedelta, timezone
import secrets
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash

from app import seed_data
from app.extensions import db
from app.models import Administrator, AvailabilitySlot, Professional, Service


SERVICES = (
    ("psicologia-clinica", "Psicología Clínica"),
    ("psicologia-forense", "Psicología Forense"),
    ("psicologia-organizacional", "Psicología Organizacional"),
    ("asesoria-legal", "Asesoría Legal"),
    ("orientacion-nutricional", "Orientación Nutricional"),
)

PROFESSIONAL_SERVICE = {
    "Apolaya Paucar, Jaime Martín": "psicologia-clinica",
    "Huarcaya Sifuentes, Kevin Albert": "psicologia-forense",
    "Quelca Apari, Cesar Anderson": "psicologia-organizacional",
}


def add_migrated_services(app):
    """Prepara las filas que en una base real crea la migración inicial."""
    with app.app_context():
        db.session.add_all(
            Service(
                slug=slug,
                name=name,
                description="Descripción institucional de demostración.",
            )
            for slug, name in SERVICES
        )
        db.session.commit()


def run_seed(app):
    return app.test_cli_runner().invoke(args=["seed-initial-data"])


def test_carga_inicial_crea_perfiles_relaciones_y_horarios_compatibles(app):
    add_migrated_services(app)

    result = run_seed(app)

    assert result.exit_code == 0, result.output
    with app.app_context():
        professionals = db.session.scalars(
            select(Professional).order_by(Professional.full_name)
        ).all()
        assert len(professionals) == 3
        assert {
            professional.full_name: [service.slug for service in professional.services]
            for professional in professionals
        } == {name: [slug] for name, slug in PROFESSIONAL_SERVICE.items()}
        assert all(professional.description == "Psicólogo - Perfil" for professional in professionals)

        slots = db.session.scalars(select(AvailabilitySlot)).all()
        assert len(slots) == 18
        lima = ZoneInfo("America/Lima")
        local_starts = []
        for slot in slots:
            stored_start = slot.starts_at
            if stored_start.tzinfo is None:
                stored_start = stored_start.replace(tzinfo=timezone.utc)
            local_start = stored_start.astimezone(lima)
            local_starts.append((local_start.date(), local_start.time().replace(tzinfo=None)))
            assert slot.ends_at - slot.starts_at == timedelta(hours=1)

        assert {day for day, _ in local_starts} == {
            date(2027, 1, 4), date(2027, 1, 5), date(2027, 1, 6)
        }
        assert {start for _, start in local_starts} == {time(9), time(10)}


def test_carga_repetida_no_duplica_ni_sobrescribe_registros(app):
    add_migrated_services(app)
    first_result = run_seed(app)
    assert first_result.exit_code == 0, first_result.output

    with app.app_context():
        professional = db.session.scalar(
            select(Professional).where(
                Professional.full_name == "Apolaya Paucar, Jaime Martín"
            )
        )
        professional.description = "Descripción existente que debe conservarse."
        professional.is_active = False
        sample_slot = db.session.scalar(select(AvailabilitySlot).limit(1))
        sample_slot.is_enabled = False
        sample_slot_id = sample_slot.id
        db.session.commit()

    second_result = run_seed(app)
    assert second_result.exit_code == 0, second_result.output

    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Professional)) == 3
        assert db.session.scalar(select(func.count()).select_from(AvailabilitySlot)) == 18
        professional = db.session.scalar(
            select(Professional).where(
                Professional.full_name == "Apolaya Paucar, Jaime Martín"
            )
        )
        assert professional.description == "Descripción existente que debe conservarse."
        assert professional.is_active is False
        assert db.session.scalar(
            select(AvailabilitySlot.is_enabled).where(AvailabilitySlot.id == sample_slot_id)
        ) is False


def test_comando_admin_guarda_hash_y_no_repite_ni_reemplaza_cuenta(app):
    password = secrets.token_urlsafe(24)
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["create-initial-admin"],
        input=f"ADMIN@example.test\n{password}\n{password}\n",
    )

    assert result.exit_code == 0, result.output
    assert password not in result.output
    with app.app_context():
        administrator = db.session.scalar(select(Administrator))
        assert administrator.email == "admin@example.test"
        assert administrator.password_hash != password
        assert check_password_hash(administrator.password_hash, password)
        original_hash = administrator.password_hash

    repeat_result = runner.invoke(args=["create-initial-admin"], input="")
    assert repeat_result.exit_code == 0, repeat_result.output
    with app.app_context():
        administrators = db.session.scalars(select(Administrator)).all()
        assert len(administrators) == 1
        assert administrators[0].password_hash == original_hash


def test_comando_admin_rechaza_clave_debil_sin_crear_cuenta(app):
    password = secrets.token_hex(2)
    result = app.test_cli_runner().invoke(
        args=["create-initial-admin"],
        input=f"admin@example.test\n{password}\n{password}\n",
    )

    assert result.exit_code != 0
    assert password not in result.output
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Administrator)) == 0


def test_comandos_de_carga_rechazan_dialectos_que_no_sean_sqlite(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(db.engine.dialect, "name", "postgresql")

    result = app.test_cli_runner().invoke(args=["seed-initial-data"])

    assert result.exit_code != 0
    assert "únicamente para SQLite local" in result.output


def test_comando_postgresql_rechaza_sqlite_sin_pedir_credenciales(app):
    result = app.test_cli_runner().invoke(args=["create-postgresql-admin"])

    assert result.exit_code != 0
    assert "únicamente para PostgreSQL" in result.output
    assert "Correo electrónico" not in result.output


def test_comando_postgresql_crea_hash_y_no_muestra_contrasena(app, monkeypatch):
    password = secrets.token_urlsafe(24)
    lock_calls = []
    with app.app_context():
        monkeypatch.setattr(db.engine.dialect, "name", "postgresql")
    monkeypatch.setattr(
        seed_data,
        "_lock_postgresql_admin_creation",
        lambda: lock_calls.append("adquirido"),
    )

    result = app.test_cli_runner().invoke(
        args=["create-postgresql-admin"],
        input=f"ADMIN@example.test\n{password}\n{password}\n",
    )

    assert result.exit_code == 0, result.output
    assert password not in result.output
    assert lock_calls == ["adquirido"]
    with app.app_context():
        administrator = db.session.scalar(select(Administrator))
        assert administrator.email == "admin@example.test"
        assert administrator.password_hash != password
        assert check_password_hash(administrator.password_hash, password)


def test_comando_postgresql_no_sobrescribe_admin_existente(app, monkeypatch):
    with app.app_context():
        administrator = Administrator(
            email="original@example.test",
            password_hash=secrets.token_urlsafe(32),
        )
        db.session.add(administrator)
        db.session.commit()
        original_hash = administrator.password_hash
        monkeypatch.setattr(db.engine.dialect, "name", "postgresql")

    def unexpected_lock():
        raise AssertionError("No debe tomar el bloqueo si ya hay una cuenta.")

    monkeypatch.setattr(seed_data, "_lock_postgresql_admin_creation", unexpected_lock)
    result = app.test_cli_runner().invoke(args=["create-postgresql-admin"], input="")

    assert result.exit_code == 0, result.output
    assert "Ya existe una cuenta administrativa" in result.output
    with app.app_context():
        administrators = db.session.scalars(select(Administrator)).all()
        assert len(administrators) == 1
        assert administrators[0].email == "original@example.test"
        assert administrators[0].password_hash == original_hash


def test_comando_postgresql_rechaza_contrasena_corta_y_confirmacion_distinta(
    app, monkeypatch
):
    with app.app_context():
        monkeypatch.setattr(db.engine.dialect, "name", "postgresql")
    monkeypatch.setattr(seed_data, "_lock_postgresql_admin_creation", lambda: None)
    runner = app.test_cli_runner()

    short_password = secrets.token_hex(4)
    short_result = runner.invoke(
        args=["create-postgresql-admin"],
        input=f"admin@example.test\n{short_password}\n{short_password}\n",
    )
    assert short_result.exit_code != 0
    assert short_password not in short_result.output

    password = secrets.token_urlsafe(24)
    mismatch_result = runner.invoke(
        args=["create-postgresql-admin"],
        input=f"admin@example.test\n{password}\n{password}x\n",
    )
    assert mismatch_result.exit_code != 0
    assert password not in mismatch_result.output
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Administrator)) == 0


def test_comando_postgresql_revierte_transaccion_si_falla_el_bloqueo(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(db.engine.dialect, "name", "postgresql")
    monkeypatch.setattr(
        seed_data,
        "_lock_postgresql_admin_creation",
        lambda: (_ for _ in ()).throw(SQLAlchemyError("fallo simulado")),
    )
    password = secrets.token_urlsafe(24)
    result = app.test_cli_runner().invoke(
        args=["create-postgresql-admin"],
        input=f"admin@example.test\n{password}\n{password}\n",
    )

    assert result.exit_code != 0
    assert "la operación se revirtió" in result.output
    assert password not in result.output
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Administrator)) == 0
