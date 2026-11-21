import re
import secrets
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Administrator,
    Appointment,
    AppointmentStatusHistory,
    AvailabilitySlot,
    Professional,
    Service,
)


BASE_URL = "https://localhost"
PASSWORD = "secure-test-password-12"
LIMA = ZoneInfo("America/Lima")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def admin_workspace(app):
    app.config.update(
        SECRET_KEY=secrets.token_urlsafe(32), SESSION_COOKIE_SECURE=True
    )
    with app.app_context():
        administrator = Administrator(
            email="owner@example.test", password_hash=generate_password_hash(PASSWORD)
        )
        service = Service(
            slug="servicio-prueba",
            name="Servicio de prueba",
            description="Descripción ficticia.",
            display_order=1,
        )
        other_service = Service(
            slug="otro-servicio",
            name="Otro servicio",
            description="Descripción ficticia.",
            display_order=2,
        )
        db.session.add_all([administrator, service, other_service])
        db.session.commit()
        service_ids = (service.id, other_service.id)

    client = app.test_client()
    login_page = client.get("/admin/login", base_url=BASE_URL)
    response = client.post(
        "/admin/login",
        base_url=BASE_URL,
        data={
            "csrf_token": csrf(login_page),
            "email": "owner@example.test",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 302
    return app, client, service_ids


def csrf(response):
    match = re.search(
        r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)
    )
    assert match
    return match.group(1)


def add_professional(app, service_id, *, email="professional@example.test", active=True):
    with app.app_context():
        service = db.session.get(Service, service_id)
        professional = Professional(
            full_name="Profesional de prueba",
            email=email,
            is_active=active,
            services=[service],
        )
        db.session.add(professional)
        db.session.commit()
        return professional.id


def add_pending_appointment(app, service_id):
    with app.app_context():
        appointment = Appointment(
            service_id=service_id,
            modality="virtual",
            contact_name="Solicitante ficticio",
            contact_email="requester@example.test",
            public_ticket_hash=secrets.token_hex(32),
            status="pending",
            professional_id=None,
            availability_slot_id=None,
            hold_expires_at=None,
        )
        db.session.add(appointment)
        db.session.commit()
        return appointment.id


def future_slot_input():
    start = datetime.now(LIMA) + timedelta(days=4)
    start = start.replace(hour=10, minute=0, second=0, microsecond=0)
    return start


def test_gestion_profesionales_crea_edita_y_desactiva_sin_eliminar(admin_workspace):
    app, client, service_ids = admin_workspace
    page = client.get("/admin/professionals/new", base_url=BASE_URL)
    response = client.post(
        "/admin/professionals/new",
        base_url=BASE_URL,
        data={
            "csrf_token": csrf(page),
            "full_name": "  Nombre   Profesional ",
            "email": " New.Person@Example.Test ",
            "service_ids": [str(service_ids[0])],
        },
    )
    assert response.status_code == 302
    with app.app_context():
        professional = db.session.scalar(
            db.select(Professional).where(
                Professional.email == "new.person@example.test"
            )
        )
        professional_id = professional.id
        assert professional.full_name == "Nombre Profesional"
        assert [service.id for service in professional.services] == [service_ids[0]]

    edit_page = client.get(
        f"/admin/professionals/{professional_id}/edit", base_url=BASE_URL
    )
    response = client.post(
        f"/admin/professionals/{professional_id}/edit",
        base_url=BASE_URL,
        data={
            "csrf_token": csrf(edit_page),
            "full_name": "Nombre actualizado",
            "email": "new.person@example.test",
            "service_ids": [str(service_ids[0]), str(service_ids[1])],
        },
    )
    assert response.status_code == 302
    page = client.get("/admin/professionals", base_url=BASE_URL)
    response = client.post(
        f"/admin/professionals/{professional_id}/deactivate",
        base_url=BASE_URL,
        data={"csrf_token": csrf(page)},
    )
    assert response.status_code == 302
    with app.app_context():
        professional = db.session.get(Professional, professional_id)
        assert professional is not None
        assert professional.full_name == "Nombre actualizado"
        assert professional.is_active is False
        assert len(professional.services) == 2


def test_profesional_rechaza_correo_invalido_duplicado_y_post_sin_csrf(admin_workspace):
    app, client, service_ids = admin_workspace
    existing_id = add_professional(app, service_ids[0])
    page = client.get("/admin/professionals/new", base_url=BASE_URL)
    for email in ("bad-email", "PROFESSIONAL@example.test"):
        response = client.post(
            "/admin/professionals/new",
            base_url=BASE_URL,
            data={
                "csrf_token": csrf(page),
                "full_name": "Otro profesional",
                "email": email,
                "service_ids": str(service_ids[0]),
            },
        )
        assert response.status_code == 400
    response = client.post(
        "/admin/professionals/new",
        base_url=BASE_URL,
        data={"full_name": "Sin CSRF", "email": "x@example.test"},
    )
    assert response.status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Professional.id))) == 1
        assert db.session.get(Professional, existing_id) is not None


def test_profesionales_requiere_autenticacion(app):
    client = app.test_client()
    for method, path in (
        ("get", "/admin/professionals"),
        ("get", "/admin/professionals/new"),
        ("post", "/admin/professionals/1/deactivate"),
        ("get", "/admin/appointments/1"),
        ("post", "/admin/appointments/1/assign"),
        ("post", "/admin/appointments/1/reject"),
    ):
        response = getattr(client, method)(path, base_url=BASE_URL)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/admin/login")


def test_asignacion_crea_bloque_confirma_y_registra_historial(admin_workspace):
    app, client, service_ids = admin_workspace
    professional_id = add_professional(app, service_ids[0])
    appointment_id = add_pending_appointment(app, service_ids[0])
    page = client.get(f"/admin/appointments/{appointment_id}", base_url=BASE_URL)
    start = future_slot_input()
    response = client.post(
        f"/admin/appointments/{appointment_id}/assign",
        base_url=BASE_URL,
        data={
            "csrf_token": csrf(page),
            "professional_id": str(professional_id),
            "date": start.date().isoformat(),
            "time": start.strftime("%H:%M"),
        },
    )
    assert response.status_code == 302
    with app.app_context():
        appointment = db.session.get(Appointment, appointment_id)
        assert appointment.status == "confirmed"
        assert appointment.professional_id == professional_id
        assert appointment.hold_expires_at is None
        slot = db.session.get(AvailabilitySlot, appointment.availability_slot_id)
        assert slot.ends_at - slot.starts_at == timedelta(hours=1)
        history = db.session.scalars(
            db.select(AppointmentStatusHistory).where(
                AppointmentStatusHistory.appointment_id == appointment_id
            )
        ).one()
        assert (history.previous_status, history.new_status) == ("pending", "confirmed")
        assert history.change_source == "administrator"


def test_asignacion_rechaza_profesional_incompatible_y_horario_solapado(admin_workspace):
    app, client, service_ids = admin_workspace
    compatible_id = add_professional(app, service_ids[0])
    incompatible_id = add_professional(
        app, service_ids[1], email="incompatible@example.test"
    )
    start = future_slot_input()
    with app.app_context():
        db.session.add(
            AvailabilitySlot(
                professional_id=compatible_id,
                starts_at=start,
                ends_at=start + timedelta(hours=1),
                is_enabled=True,
            )
        )
        db.session.commit()

    incompatible_appointment = add_pending_appointment(app, service_ids[0])
    page = client.get(
        f"/admin/appointments/{incompatible_appointment}", base_url=BASE_URL
    )
    response = client.post(
        f"/admin/appointments/{incompatible_appointment}/assign",
        base_url=BASE_URL,
        data={
            "csrf_token": csrf(page),
            "professional_id": str(incompatible_id),
            "date": start.date().isoformat(),
            "time": "12:00",
        },
    )
    assert response.status_code == 302

    overlap_appointment = add_pending_appointment(app, service_ids[0])
    page = client.get(f"/admin/appointments/{overlap_appointment}", base_url=BASE_URL)
    response = client.post(
        f"/admin/appointments/{overlap_appointment}/assign",
        base_url=BASE_URL,
        data={
            "csrf_token": csrf(page),
            "professional_id": str(compatible_id),
            "date": start.date().isoformat(),
            "time": "10:30",
        },
        follow_redirects=True,
    )
    assert "no se pudo reservar ese horario" in response.get_data(as_text=True).lower()
    with app.app_context():
        assert db.session.get(Appointment, incompatible_appointment).status == "pending"
        assert db.session.get(Appointment, overlap_appointment).status == "pending"


def test_rechazo_guarda_estado_historial_y_requiere_csrf(admin_workspace):
    app, client, service_ids = admin_workspace
    appointment_id = add_pending_appointment(app, service_ids[0])
    response = client.post(
        f"/admin/appointments/{appointment_id}/reject",
        base_url=BASE_URL,
        data={},
    )
    assert response.status_code == 400
    page = client.get(f"/admin/appointments/{appointment_id}", base_url=BASE_URL)
    response = client.post(
        f"/admin/appointments/{appointment_id}/reject",
        base_url=BASE_URL,
        data={"csrf_token": csrf(page)},
    )
    assert response.status_code == 302
    with app.app_context():
        appointment = db.session.get(Appointment, appointment_id)
        history = db.session.scalars(
            db.select(AppointmentStatusHistory).where(
                AppointmentStatusHistory.appointment_id == appointment_id
            )
        ).one()
        assert appointment.status == "rejected"
        assert appointment.professional_id is None
        assert history.new_status == "rejected"


def test_dos_asignaciones_simultaneas_no_crean_horarios_solapados(admin_workspace):
    app, first_client, service_ids = admin_workspace
    professional_id = add_professional(app, service_ids[0])
    appointment_ids = [
        add_pending_appointment(app, service_ids[0]),
        add_pending_appointment(app, service_ids[0]),
    ]
    second_client = app.test_client()
    login_page = second_client.get("/admin/login", base_url=BASE_URL)
    login_response = second_client.post(
        "/admin/login",
        base_url=BASE_URL,
        data={
            "csrf_token": csrf(login_page),
            "email": "owner@example.test",
            "password": PASSWORD,
        },
    )
    assert login_response.status_code == 302

    start = future_slot_input()
    clients = (first_client, second_client)
    requests = []
    for client, appointment_id in zip(clients, appointment_ids):
        page = client.get(
            f"/admin/appointments/{appointment_id}", base_url=BASE_URL
        )
        requests.append(
            (
                client,
                appointment_id,
                csrf(page),
                {"date": start.date().isoformat(), "time": start.strftime("%H:%M")},
            )
        )
    barrier = Barrier(2)

    def submit_assignment(item):
        client, appointment_id, token, slot_data = item
        barrier.wait()
        return client.post(
            f"/admin/appointments/{appointment_id}/assign",
            base_url=BASE_URL,
            data={
                "csrf_token": token,
                "professional_id": str(professional_id),
                **slot_data,
            },
            follow_redirects=True,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit_assignment, requests))
    assert all(response.status_code == 200 for response in responses)
    with app.app_context():
        statuses = [
            appointment.status
            for appointment in db.session.scalars(
                db.select(Appointment).where(Appointment.id.in_(appointment_ids))
            )
        ]
        assert statuses.count("confirmed") == 1
        assert statuses.count("pending") == 1
        assert db.session.scalar(
            db.select(db.func.count(AvailabilitySlot.id)).where(
                AvailabilitySlot.professional_id == professional_id
            )
        ) == 1


def test_migracion_agrega_correo_nullable_sin_perder_profesionales(tmp_path):
    database_file = tmp_path / "professional-email-migration.sqlite3"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database_file.as_posix()}"
    environment["FLASK_DEBUG"] = "false"
    initial_upgrade = subprocess.run(
        [
            sys.executable, "-m", "flask", "--app", "run.py", "db", "upgrade",
            "b64ac30f76d1",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert initial_upgrade.returncode == 0, initial_upgrade.stdout + initial_upgrade.stderr
    with sqlite3.connect(database_file) as connection:
        connection.execute(
            "INSERT INTO professionals (full_name) VALUES ('Perfil anterior')"
        )
    final_upgrade = subprocess.run(
        [sys.executable, "-m", "flask", "--app", "run.py", "db", "upgrade"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert final_upgrade.returncode == 0, final_upgrade.stdout + final_upgrade.stderr
    with sqlite3.connect(database_file) as connection:
        assert connection.execute("SELECT COUNT(*) FROM professionals").fetchone()[0] == 1
        assert connection.execute("SELECT email FROM professionals").fetchone()[0] is None
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "c2a8e6149f30"
        connection.execute(
            "UPDATE professionals SET email='contact@example.test' WHERE full_name=?",
            ("Perfil anterior",),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO professionals (full_name, email) VALUES (?, ?)",
                ("Perfil duplicado", "CONTACT@example.test"),
            )
