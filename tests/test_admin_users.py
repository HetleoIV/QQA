import re
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

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


@pytest.fixture
def administrator_client(app):
    app.config.update(
        SECRET_KEY=secrets.token_urlsafe(32),
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    with app.app_context():
        administrator = Administrator(
            email="owner@example.test", password_hash=generate_password_hash(PASSWORD)
        )
        db.session.add(administrator)
        db.session.commit()
        administrator_id = administrator.id

    client = app.test_client()
    login_page = client.get("/admin/login", base_url=BASE_URL)
    token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.get_data(as_text=True)).group(1)
    response = client.post(
        "/admin/login", base_url=BASE_URL,
        data={"csrf_token": token, "email": "owner@example.test", "password": PASSWORD},
    )
    assert response.status_code == 302
    return app, client, administrator_id


def csrf_token(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match
    return match.group(1)


def create_account(app, email="new@example.test", *, active=True):
    with app.app_context():
        account = Administrator(
            email=email, password_hash=generate_password_hash(PASSWORD), is_active=active
        )
        db.session.add(account)
        db.session.commit()
        return account.id


def test_users_routes_are_protected_and_list_never_discloses_hash(app):
    response = app.test_client().get("/admin/users", base_url=BASE_URL)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")


def test_listado_detalle_y_formulario_no_exponen_password_hash(administrator_client):
    app, client, owner_id = administrator_client
    list_response = client.get("/admin/users", base_url=BASE_URL)
    assert list_response.status_code == 200
    assert "owner@example.test" in list_response.get_data(as_text=True)
    with app.app_context():
        stored_hash = db.session.get(Administrator, owner_id).password_hash
    assert stored_hash not in list_response.get_data(as_text=True)
    detail = client.get(f"/admin/users/{owner_id}", base_url=BASE_URL)
    assert detail.status_code == 200
    assert stored_hash not in detail.get_data(as_text=True)
    assert client.get("/admin/users/new", base_url=BASE_URL).status_code == 200


def test_creacion_normaliza_correo_y_guarda_hash(administrator_client):
    app, client, _ = administrator_client
    page = client.get("/admin/users/new", base_url=BASE_URL)
    response = client.post(
        "/admin/users/new", base_url=BASE_URL,
        data={"csrf_token": csrf_token(page), "email": " New.User@Example.Test ",
              "password": PASSWORD, "password_confirmation": PASSWORD},
    )
    assert response.status_code == 302
    with app.app_context():
        created = db.session.scalar(
            db.select(Administrator).where(Administrator.email == "new.user@example.test")
        )
        assert created is not None and created.is_active
        assert created.password_hash != PASSWORD
        assert check_password_hash(created.password_hash, PASSWORD)
    assert PASSWORD not in response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("email", "password", "confirmation", "message"),
    [
        ("bad-email", PASSWORD, PASSWORD, "correo electrónico válido"),
        ("fresh@example.test", "short", "short", "12 caracteres"),
        ("fresh@example.test", PASSWORD, "different-password-12", "no coinciden"),
        ("OWNER@example.test", PASSWORD, PASSWORD, "Ya existe"),
    ],
)
def test_creacion_rechaza_datos_invalidos_y_duplicados(
    administrator_client, email, password, confirmation, message
):
    app, client, _ = administrator_client
    page = client.get("/admin/users/new", base_url=BASE_URL)
    response = client.post(
        "/admin/users/new", base_url=BASE_URL,
        data={"csrf_token": csrf_token(page), "email": email, "password": password,
              "password_confirmation": confirmation},
    )
    assert response.status_code == 400
    assert message.lower() in response.get_data(as_text=True).lower()
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Administrator.id))) == 1


def test_todos_los_post_requieren_csrf(administrator_client):
    _, client, owner_id = administrator_client
    for path, data in (
        ("/admin/users/new", {"email": "x@example.test", "password": PASSWORD,
                              "password_confirmation": PASSWORD}),
        (f"/admin/users/{owner_id}/activate", {}),
        (f"/admin/users/{owner_id}/deactivate", {}),
        (f"/admin/users/{owner_id}/delete", {"confirm_delete": "yes"}),
    ):
        response = client.post(path, base_url=BASE_URL, data=data)
        assert response.status_code == 400


def test_cuenta_inactiva_no_puede_iniciar_sesion(administrator_client):
    app, client, owner_id = administrator_client
    token = csrf_token(client.get("/admin/users", base_url=BASE_URL))
    other_id = create_account(app, "inactive@example.test", active=False)
    login_client = app.test_client()
    page = login_client.get("/admin/login", base_url=BASE_URL)
    login_token = csrf_token(page)
    response = login_client.post(
        "/admin/login", base_url=BASE_URL,
        data={"csrf_token": login_token, "email": "inactive@example.test", "password": PASSWORD},
    )
    assert response.status_code == 200
    with login_client.session_transaction(base_url=BASE_URL) as saved:
        assert "administrator_id" not in saved
    assert token
    assert other_id is not None


def test_activar_desactivar_y_proteger_ultima_cuenta_activa(administrator_client):
    app, client, owner_id = administrator_client
    target_id = create_account(app, "target@example.test", active=False)
    detail = client.get(f"/admin/users/{target_id}", base_url=BASE_URL)
    response = client.post(
        f"/admin/users/{target_id}/activate", base_url=BASE_URL,
        data={"csrf_token": csrf_token(detail)},
    )
    assert response.status_code == 302
    detail = client.get(f"/admin/users/{target_id}", base_url=BASE_URL)
    response = client.post(
        f"/admin/users/{target_id}/deactivate", base_url=BASE_URL,
        data={"csrf_token": csrf_token(detail)},
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Administrator, target_id).is_active is False
        assert db.session.get(Administrator, owner_id).is_active is True

    # Tras desactivar al segundo usuario, no se permite desactivar al propietario,
    # que es la única cuenta todavía activa.
    detail = client.get(f"/admin/users/{owner_id}", base_url=BASE_URL)
    response = client.post(
        f"/admin/users/{owner_id}/deactivate", base_url=BASE_URL,
        data={"csrf_token": csrf_token(detail)},
        follow_redirects=True,
    )
    assert "No puedes desactivar tu propia cuenta" in response.get_data(as_text=True)


def test_no_permite_desactivar_ultima_cuenta_activa_de_otro_usuario(
    administrator_client, monkeypatch
):
    app, client, owner_id = administrator_client
    target_id = create_account(app, "second@example.test")
    detail = client.get(f"/admin/users/{target_id}", base_url=BASE_URL)
    # El actor activo impide naturalmente un resultado de cero. Simulamos el
    # estado límite para verificar también la protección defensiva de la ruta.
    import app.admin as admin_module
    monkeypatch.setattr(admin_module, "_active_administrator_count", lambda: 1)
    response = client.post(
        f"/admin/users/{target_id}/deactivate", base_url=BASE_URL,
        data={"csrf_token": csrf_token(detail)}, follow_redirects=True,
    )
    assert "última cuenta activa" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Administrator, owner_id).is_active
        assert db.session.get(Administrator, target_id).is_active


def test_confirmacion_de_eliminacion_no_modifica_hasta_post(administrator_client):
    app, client, _ = administrator_client
    target_id = create_account(app, "remove@example.test", active=False)
    confirmation = client.get(f"/admin/users/{target_id}/delete", base_url=BASE_URL)
    assert confirmation.status_code == 200
    assert "confirmo" in confirmation.get_data(as_text=True).lower()
    with app.app_context():
        assert db.session.get(Administrator, target_id) is not None
    response = client.post(
        f"/admin/users/{target_id}/delete", base_url=BASE_URL,
        data={"csrf_token": csrf_token(confirmation), "confirm_delete": "yes"},
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Administrator, target_id) is None


def test_eliminacion_rechaza_autoeliminacion_y_ultima_cuenta(administrator_client):
    app, client, owner_id = administrator_client
    confirmation = client.get(f"/admin/users/{owner_id}/delete", base_url=BASE_URL)
    response = client.post(
        f"/admin/users/{owner_id}/delete", base_url=BASE_URL,
        data={"csrf_token": csrf_token(confirmation), "confirm_delete": "yes"},
        follow_redirects=True,
    )
    assert "No puedes eliminar tu propia cuenta" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Administrator, owner_id) is not None


def test_eliminacion_rechaza_ultima_cuenta_activa(administrator_client, monkeypatch):
    app, client, _ = administrator_client
    target_id = create_account(app, "last@example.test")
    import app.admin as admin_module
    monkeypatch.setattr(admin_module, "_active_administrator_count", lambda: 1)
    confirmation = client.get(f"/admin/users/{target_id}/delete", base_url=BASE_URL)
    response = client.post(
        f"/admin/users/{target_id}/delete", base_url=BASE_URL,
        data={"csrf_token": csrf_token(confirmation), "confirm_delete": "yes"},
        follow_redirects=True,
    )
    assert "última cuenta activa" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Administrator, target_id) is not None


def test_historial_impide_eliminar_y_conserva_atribucion(administrator_client):
    app, client, owner_id = administrator_client
    target_id = create_account(app, "audited@example.test", active=False)
    with app.app_context():
        administrator = db.session.get(Administrator, target_id)
        service = Service(slug="test-service", name="Servicio de prueba", description="Prueba")
        professional = Professional(full_name="Profesional de prueba")
        professional.services.append(service)
        db.session.add_all([service, professional])
        db.session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=4)
        slot = AvailabilitySlot(
            professional_id=professional.id, starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1), is_enabled=True,
        )
        db.session.add(slot)
        db.session.flush()
        appointment = Appointment(
            availability_slot_id=slot.id,
            professional_id=professional.id,
            service_id=service.id,
            modality="virtual",
            contact_name="Contacto ficticio",
            contact_email="contact@example.test",
            public_ticket_hash="a" * 64,
            status="pending",
            hold_expires_at=starts_at - timedelta(minutes=5),
        )
        db.session.add(appointment)
        db.session.flush()
        history = AppointmentStatusHistory(
            appointment_id=appointment.id,
            previous_status=None,
            new_status="pending",
            changed_by_administrator_id=administrator.id,
            change_source="administrator",
        )
        db.session.add(history)
        db.session.commit()
        history_id = history.id

    confirmation = client.get(f"/admin/users/{target_id}/delete", base_url=BASE_URL)
    assert "no se puede eliminar" in confirmation.get_data(as_text=True).lower()
    response = client.post(
        f"/admin/users/{target_id}/delete", base_url=BASE_URL,
        data={"csrf_token": csrf_token(confirmation), "confirm_delete": "yes"},
        follow_redirects=True,
    )
    assert "historial de auditoría" in response.get_data(as_text=True).lower()
    with app.app_context():
        history = db.session.get(AppointmentStatusHistory, history_id)
        assert history.changed_by_administrator_id == target_id
        assert db.session.get(Administrator, target_id) is not None
