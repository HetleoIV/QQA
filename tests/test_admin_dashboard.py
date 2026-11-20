import re
import secrets
from datetime import datetime, timezone

import pytest
from werkzeug.security import generate_password_hash

from app import admin as admin_module
from app.extensions import db
from app.models import Administrator


@pytest.fixture
def admin_auth(app):
    app.config["SECRET_KEY"] = secrets.token_urlsafe(32)
    email = "dashboard@example.test"
    password = secrets.token_urlsafe(24)
    with app.app_context():
        administrator = Administrator(
            email=email,
            password_hash=generate_password_hash(password),
            is_active=True,
        )
        db.session.add(administrator)
        db.session.commit()
        administrator_id = administrator.id
    return app, app.test_client(), email, password, administrator_id


def login_admin(client, email, password):
    login_page = client.get("/admin/login", base_url="https://localhost")
    token = re.search(
        r'name="csrf_token" value="([^"]+)"',
        login_page.get_data(as_text=True),
    ).group(1)
    return client.post(
        "/admin/login",
        base_url="https://localhost",
        data={"csrf_token": token, "email": email, "password": password},
    )


def test_dashboard_muestra_vista_previa_y_secciones_solicitadas(admin_auth):
    _app, client, email, password, _administrator_id = admin_auth
    login_admin(client, email, password)

    response = client.get("/admin/dashboard", base_url="https://localhost")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Vista previa - Datos de demostración" in page
    assert "Citas totales" in page
    assert "Solicitudes pendientes" in page
    assert "Citas confirmadas" in page
    assert "Profesionales registrados" in page
    assert "Próximas citas" in page
    assert "Actividad reciente" in page
    assert "Solicitud de demostración" in page


def test_dashboard_muestra_navegacion_y_conserva_logout_con_csrf(admin_auth):
    _app, client, email, password, _administrator_id = admin_auth
    login_admin(client, email, password)

    response = client.get("/admin/dashboard", base_url="https://localhost")
    page = response.get_data(as_text=True)

    for item in (
        "Resumen", "Citas", "Profesionales", "Servicios", "Disponibilidad",
        "Notificaciones", "Cerrar sesión",
    ):
        assert item in page
    assert 'name="csrf_token"' in page
    assert 'action="/admin/logout"' in page


def test_fecha_del_dashboard_usa_la_zona_horaria_de_peru(admin_auth, monkeypatch):
    _app, client, email, password, _administrator_id = admin_auth
    login_admin(client, email, password)

    class FixedDateTime:
        @classmethod
        def now(cls, timezone_info):
            assert timezone_info.key == "America/Lima"
            utc_time = datetime(2026, 11, 19, 1, 30, tzinfo=timezone.utc)
            return utc_time.astimezone(timezone_info)

    monkeypatch.setattr(admin_module, "datetime", FixedDateTime)

    page = client.get("/admin/dashboard", base_url="https://localhost").get_data(
        as_text=True
    )

    assert "miércoles, 18 de noviembre de 2026" in page
