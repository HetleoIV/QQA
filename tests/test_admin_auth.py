import re
import secrets

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import Administrator


BASE_URL = "https://localhost"
INVALID_CREDENTIALS = "Correo electrónico o contraseña incorrectos."


@pytest.fixture
def admin_auth(app):
    app.config.update(
        SECRET_KEY=secrets.token_urlsafe(32),
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    email = "admin@example.test"
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


def csrf_token_from(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match is not None
    return match.group(1)


def authenticate(client, email, password, *, path="/admin/login"):
    login_page = client.get("/admin/login", base_url=BASE_URL)
    token = csrf_token_from(login_page)
    return client.post(
        path,
        base_url=BASE_URL,
        data={"csrf_token": token, "email": email, "password": password},
    )


def test_login_correcto_crea_sesion_segura_y_redirige_al_dashboard(admin_auth):
    app, client, email, password, administrator_id = admin_auth

    response = authenticate(
        client,
        email,
        password,
        path="/admin/login?next=https://sitio-externo.example/",
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/dashboard")
    cookie_header = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie_header
    assert "Secure" in cookie_header
    assert "SameSite=Lax" in cookie_header
    with client.session_transaction(base_url=BASE_URL) as saved_session:
        assert saved_session["administrator_id"] == administrator_id
        assert saved_session.permanent is True

    with app.app_context():
        administrator = db.session.get(Administrator, administrator_id)
        assert administrator.password_hash != password
        assert check_password_hash(administrator.password_hash, password)


def test_logo_y_volver_al_inicio_en_login_apuntan_a_la_pagina_publica(admin_auth):
    _app, client, _email, _password, _administrator_id = admin_auth

    response = client.get("/admin/login", base_url=BASE_URL)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'class="brand" href="/#inicio"' in page
    assert 'class="footer-top" href="/#inicio"' in page
    assert client.get("/", base_url=BASE_URL).status_code == 200


@pytest.mark.parametrize(
    ("email", "password_kind"),
    [
        ("admin@example.test", "wrong"),
        ("unknown@example.test", "correct"),
    ],
)
def test_credenciales_invalidas_y_usuario_inexistente_reciben_mensaje_generico(
    admin_auth, email, password_kind
):
    _, client, known_email, known_password, _ = admin_auth
    password = "invalid-password" if password_kind == "wrong" else known_password

    response = authenticate(client, email, password)

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert INVALID_CREDENTIALS in page
    assert "correo no existe" not in page.lower()
    assert "contraseña incorrecta" not in page.lower()
    with client.session_transaction(base_url=BASE_URL) as saved_session:
        assert "administrator_id" not in saved_session
    assert known_email == "admin@example.test"


def test_dashboard_requiere_sesion_y_sesion_persiste_entre_peticiones(admin_auth):
    _, client, email, password, _ = admin_auth

    anonymous_response = client.get("/admin/dashboard", base_url=BASE_URL)
    assert anonymous_response.status_code == 302
    assert anonymous_response.headers["Location"].endswith("/admin/login")

    login_response = authenticate(client, email, password)
    assert login_response.status_code == 302
    first_dashboard = client.get("/admin/dashboard", base_url=BASE_URL)
    second_dashboard = client.get("/admin/dashboard", base_url=BASE_URL)
    assert first_dashboard.status_code == 200
    assert second_dashboard.status_code == 200
    assert "Panel administrativo" in second_dashboard.get_data(as_text=True)


def test_admin_puede_ir_al_inicio_publico_y_regresar_al_panel(admin_auth):
    _app, client, email, password, administrator_id = admin_auth
    assert authenticate(client, email, password).status_code == 302

    dashboard = client.get("/admin/dashboard", base_url=BASE_URL)
    dashboard_page = dashboard.get_data(as_text=True)
    assert 'href="/"' in dashboard_page
    assert "Volver al inicio" in dashboard_page

    homepage = client.get("/", base_url=BASE_URL)
    assert homepage.status_code == 200
    assert 'href="/admin/dashboard"' in homepage.get_data(as_text=True)
    with client.session_transaction(base_url=BASE_URL) as saved_session:
        assert saved_session["administrator_id"] == administrator_id

    returned_dashboard = client.get("/admin/dashboard", base_url=BASE_URL)
    assert returned_dashboard.status_code == 200
    assert "Panel administrativo" in returned_dashboard.get_data(as_text=True)


def test_enlace_administrativo_publico_no_permite_entrar_sin_autenticacion(app):
    app.config["SECRET_KEY"] = secrets.token_urlsafe(32)
    client = app.test_client()

    homepage = client.get("/", base_url=BASE_URL)
    assert 'href="/admin/dashboard"' in homepage.get_data(as_text=True)

    response = client.get("/admin/dashboard", base_url=BASE_URL)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")


def test_cerrar_sesion_requiere_csrf_y_elimina_la_sesion(admin_auth):
    _, client, email, password, administrator_id = admin_auth
    assert authenticate(client, email, password).status_code == 302

    dashboard = client.get("/admin/dashboard", base_url=BASE_URL)
    token = csrf_token_from(dashboard)

    missing_token = client.post("/admin/logout", base_url=BASE_URL, data={})
    invalid_token = client.post(
        "/admin/logout", base_url=BASE_URL, data={"csrf_token": "token-invalido"}
    )
    get_logout = client.get("/admin/logout", base_url=BASE_URL)
    assert missing_token.status_code == 400
    assert invalid_token.status_code == 400
    assert get_logout.status_code == 405
    with client.session_transaction(base_url=BASE_URL) as saved_session:
        assert saved_session["administrator_id"] == administrator_id

    logout_response = client.post(
        "/admin/logout", base_url=BASE_URL, data={"csrf_token": token}
    )
    assert logout_response.status_code == 302
    assert logout_response.headers["Location"].endswith("/admin/login")
    with client.session_transaction(base_url=BASE_URL) as saved_session:
        assert "administrator_id" not in saved_session

    login_page_after_logout = client.get("/admin/login", base_url=BASE_URL)
    assert "Sesión cerrada." in login_page_after_logout.get_data(as_text=True)
    assert client.get("/admin/dashboard", base_url=BASE_URL).status_code == 302
    appointments_response = client.get("/admin/appointments", base_url=BASE_URL)
    assert appointments_response.status_code == 302
    assert appointments_response.headers["Location"].endswith("/admin/login")
    assert client.get("/", base_url=BASE_URL).status_code == 200

    fresh_login_page = client.get("/admin/login", base_url=BASE_URL)
    missing_token_response = client.post(
        "/admin/login",
        base_url=BASE_URL,
        data={"email": email, "password": password},
    )
    assert fresh_login_page.status_code == 200
    assert missing_token_response.status_code == 400


def test_boton_cerrar_sesion_desde_solicitudes_envia_token_csrf(admin_auth):
    _app, client, email, password, administrator_id = admin_auth
    assert authenticate(client, email, password).status_code == 302

    appointments_page = client.get("/admin/appointments", base_url=BASE_URL)
    token = csrf_token_from(appointments_page)
    assert appointments_page.status_code == 200
    assert token

    logout_response = client.post(
        "/admin/logout", base_url=BASE_URL, data={"csrf_token": token}
    )

    assert logout_response.status_code == 302
    assert logout_response.headers["Location"].endswith("/admin/login")
    with client.session_transaction(base_url=BASE_URL) as saved_session:
        assert "administrator_id" not in saved_session
    assert client.get("/admin/dashboard", base_url=BASE_URL).status_code == 302
    assert client.get("/", base_url=BASE_URL).status_code == 200


def test_login_no_funciona_si_falta_secret_key(app):
    app.config["SECRET_KEY"] = None

    response = app.test_client().get("/admin/login")

    assert response.status_code == 503
