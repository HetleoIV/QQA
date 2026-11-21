import re
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import (
    Administrator,
    Appointment,
    AvailabilitySlot,
    Professional,
    Service,
)
from app.psychologist import censor_patient_name


BASE_URL = "https://localhost"
ADMIN_PASSWORD = "admin-password-test-123"
PSYCHOLOGIST_PASSWORD = "psychologist-password-123"
LIMA = ZoneInfo("America/Lima")


def token(response):
    found = re.search(
        r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)
    )
    assert found
    return found.group(1)


@pytest.fixture
def role_workspace(app):
    app.config.update(SECRET_KEY=secrets.token_urlsafe(32), SESSION_COOKIE_SECURE=True)
    with app.app_context():
        service = Service(
            slug="psychology-test",
            name="Servicio confidencial ficticio",
            description="Servicio de prueba.",
        )
        admin = Administrator(
            email="admin@qqa",
            password_hash=generate_password_hash(ADMIN_PASSWORD),
        )
        professional = Professional(
            full_name="Psicóloga de prueba",
            email="professional@example.test",
            services=[service],
        )
        other_professional = Professional(
            full_name="Otro profesional",
            email="other-professional@example.test",
            services=[service],
        )
        db.session.add_all([service, admin, professional, other_professional])
        db.session.commit()
        ids = (service.id, professional.id, other_professional.id, admin.id)
    return app, ids


def login(client, email, password):
    page = client.get("/admin/login", base_url=BASE_URL)
    return client.post(
        "/admin/login",
        base_url=BASE_URL,
        data={"csrf_token": token(page), "email": email, "password": password},
    )


def create_assigned_appointment(
    app, service_id, professional_id, starts_at, *, parts, full_name, email, phone, ticket
):
    with app.app_context():
        slot = AvailabilitySlot(
            professional_id=professional_id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            is_enabled=True,
        )
        db.session.add(slot)
        db.session.flush()
        appointment = Appointment(
            availability_slot_id=slot.id,
            professional_id=professional_id,
            service_id=service_id,
            modality="virtual",
            contact_name=full_name,
            first_name=parts[0],
            second_name=parts[1],
            first_surname=parts[2],
            second_surname=parts[3],
            contact_email=email,
            contact_phone=phone,
            public_ticket_hash=ticket,
            status="confirmed",
        )
        db.session.add(appointment)
        db.session.commit()
        return appointment.id


def test_admin_existente_conserva_hash_y_rol(role_workspace):
    app, (_service_id, _professional_id, _other_professional_id, admin_id) = role_workspace
    with app.app_context():
        admin = db.session.get(Administrator, admin_id)
        password_hash = admin.password_hash
        assert admin.email == "admin@qqa"
        assert admin.role == "admin"

    client = app.test_client()
    response = login(client, "admin@qqa", ADMIN_PASSWORD)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/dashboard")
    assert app.test_client().get(
        "/psychologist/appointments", base_url=BASE_URL
    ).status_code == 302
    with app.app_context():
        admin = db.session.get(Administrator, admin_id)
        assert admin.password_hash == password_hash
        assert check_password_hash(admin.password_hash, ADMIN_PASSWORD)


def test_admin_crea_cuenta_individual_de_psicologo_vinculada(role_workspace):
    app, (_service_id, professional_id, _other_professional_id, _admin_id) = role_workspace
    client = app.test_client()
    login(client, "admin@qqa", ADMIN_PASSWORD)
    page = client.get("/admin/users/new", base_url=BASE_URL)
    response = client.post(
        "/admin/users/new",
        base_url=BASE_URL,
        data={
            "csrf_token": token(page),
            "role": "psychologist",
            "professional_id": str(professional_id),
            "email": "psychologist@example.test",
            "password": PSYCHOLOGIST_PASSWORD,
            "password_confirmation": PSYCHOLOGIST_PASSWORD,
        },
    )
    assert response.status_code == 302
    with app.app_context():
        account = db.session.scalar(
            db.select(Administrator).where(
                Administrator.email == "psychologist@example.test"
            )
        )
        assert account.role == "psychologist"
        assert account.professional_id == professional_id
        account_id = account.id
        assert check_password_hash(account.password_hash, PSYCHOLOGIST_PASSWORD)
        assert account.password_hash != PSYCHOLOGIST_PASSWORD

    page = client.get(f"/admin/users/{account_id}", base_url=BASE_URL)
    response = client.post(
        f"/admin/users/{account_id}/deactivate",
        base_url=BASE_URL,
        data={"csrf_token": token(page)},
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Administrator, account_id).is_active is False
        assert db.session.get(Professional, professional_id) is not None


def test_psicologo_inicia_sesion_y_no_accede_a_rutas_admin(role_workspace):
    app, (service_id, professional_id, _other_id, _admin_id) = role_workspace
    with app.app_context():
        account = Administrator(
            email="psychologist@example.test",
            password_hash=generate_password_hash(PSYCHOLOGIST_PASSWORD),
            role="psychologist",
            professional_id=professional_id,
        )
        db.session.add(account)
        db.session.commit()
    client = app.test_client()
    response = login(client, "psychologist@example.test", PSYCHOLOGIST_PASSWORD)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/psychologist/appointments")
    for path in (
        "/admin/dashboard",
        "/admin/users",
        "/admin/professionals",
        "/admin/services",
    ):
        assert client.get(path, base_url=BASE_URL).status_code == 403
    assert client.get("/psychologist/appointments", base_url=BASE_URL).status_code == 200


def test_psicologo_solo_ve_sus_citas_y_html_sin_datos_personales(role_workspace):
    app, (service_id, professional_id, other_id, _admin_id) = role_workspace
    with app.app_context():
        db.session.add(
            Administrator(
                email="psychologist@example.test",
                password_hash=generate_password_hash(PSYCHOLOGIST_PASSWORD),
                role="psychologist",
                professional_id=professional_id,
            )
        )
        db.session.commit()
    first_time = datetime.now(LIMA) + timedelta(days=4)
    first_time = first_time.replace(minute=0, second=0, microsecond=0)
    own_id = create_assigned_appointment(
        app,
        service_id,
        professional_id,
        first_time,
        parts=("Laura", "Fernanda", "Salazar", "Becerra"),
        full_name="Laura Fernanda Salazar Becerra",
        email="patient-private@example.test",
        phone="+51 999 111 222",
        ticket="own-ticket-hash",
    )
    create_assigned_appointment(
        app,
        service_id,
        other_id,
        first_time,
        parts=("Nombre", "Secreto", "Apellido", "Oculto"),
        full_name="Nombre Secreto Apellido Oculto",
        email="other-private@example.test",
        phone="+51 999 333 444",
        ticket="other-ticket-hash",
    )
    client = app.test_client()
    login(client, "psychologist@example.test", PSYCHOLOGIST_PASSWORD)
    response = client.get("/psychologist/appointments", base_url=BASE_URL)
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert f"Laura Sala{'°' * 3}." in page
    assert first_time.strftime("%d/%m/%Y %H:%M") in page
    assert "Servicio confidencial ficticio" in page
    assert f"solicitud #{own_id}" not in page
    assert "Fernanda" not in page
    assert "Becerra" not in page
    assert "Laura Fernanda Salazar Becerra" not in page
    assert "patient-private@example.test" not in page
    assert "+51 999 111 222" not in page
    assert "Nombre Secreto Apellido Oculto" not in page
    assert "other-private@example.test" not in page


def test_censura_y_fallback_historico_no_dividen_contact_name():
    assert censor_patient_name("María", "González", 12) == "María Gonz°°°°."
    assert censor_patient_name(None, None, 44) == "Paciente · solicitud #44"


def test_logout_psicologo_requiere_csrf_y_cierra_sesion(role_workspace):
    app, (_service_id, professional_id, _other_id, _admin_id) = role_workspace
    with app.app_context():
        db.session.add(
            Administrator(
                email="psychologist@example.test",
                password_hash=generate_password_hash(PSYCHOLOGIST_PASSWORD),
                role="psychologist",
                professional_id=professional_id,
            )
        )
        db.session.commit()
    client = app.test_client()
    login(client, "psychologist@example.test", PSYCHOLOGIST_PASSWORD)
    assert client.post("/psychologist/logout", base_url=BASE_URL, data={}).status_code == 400
    page = client.get("/psychologist/appointments", base_url=BASE_URL)
    response = client.post(
        "/psychologist/logout",
        base_url=BASE_URL,
        data={"csrf_token": token(page)},
    )
    assert response.status_code == 302
    assert client.get("/psychologist/appointments", base_url=BASE_URL).headers[
        "Location"
    ].endswith("/admin/login")
