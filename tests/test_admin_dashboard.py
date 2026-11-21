import re
import secrets
from datetime import datetime, timezone

import pytest
from werkzeug.security import generate_password_hash

from app import admin as admin_module
from app.extensions import db
from app.models import Administrator, Appointment, Service


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
    assert "Solicitudes actuales · agenda de demostración" in page
    assert "Citas totales" in page
    assert "Solicitudes pendientes" in page
    assert "Citas confirmadas" in page
    assert "Profesionales registrados" in page
    assert "Próximas citas" in page
    assert "Actividad reciente" in page
    assert "Solicitud de demostración" in page


def test_dashboard_muestra_indicadores_reales_de_solicitudes(admin_auth):
    app, client, email, password, _administrator_id = admin_auth
    with app.app_context():
        service = Service(
            slug="servicio-indicadores",
            name="Servicio indicadores",
            description="Servicio ficticio de prueba.",
        )
        db.session.add(service)
        db.session.flush()
        for index, status in enumerate(("pending", "pending", "rejected")):
            db.session.add(
                Appointment(
                    service_id=service.id,
                    modality="virtual",
                    contact_name=f"Solicitante {index}",
                    contact_email=f"indicador-{index}@example.test",
                    public_ticket_hash=f"indicador-ticket-{index}",
                    status=status,
                    professional_id=None,
                    availability_slot_id=None,
                    hold_expires_at=None,
                )
            )
        db.session.commit()

    login_admin(client, email, password)
    page = client.get("/admin/dashboard", base_url="https://localhost").get_data(
        as_text=True
    )
    def metric_value(label):
        start = page.rfind("<article", 0, page.index(label))
        end = page.index("</article>", start)
        match = re.search(r'class="admin-dashboard-stat-value">(\d+)', page[start:end])
        assert match
        return match.group(1)

    assert metric_value("Citas totales") == "3"
    assert "Solicitudes recibidas" in page
    assert metric_value("Solicitudes pendientes") == "2"
    assert metric_value("Citas confirmadas") == "0"


def _add_requests(app, rows):
    with app.app_context():
        service = Service(
            slug="servicio-solicitudes",
            name="Servicio de solicitudes",
            description="Servicio ficticio para pruebas.",
        )
        db.session.add(service)
        db.session.flush()
        appointments = []
        for index, row in enumerate(rows):
            appointment = Appointment(
                service_id=service.id,
                modality=row.get("modality", "virtual"),
                contact_name=row.get("name", f"Solicitante {index}"),
                contact_email=row.get("email", f"persona-{index}@example.test"),
                contact_phone=row.get("phone", "+51 900 000 000"),
                public_ticket_hash=f"solicitud-ticket-{index}",
                status=row.get("status", "pending"),
                professional_id=None,
                availability_slot_id=None,
                hold_expires_at=None,
                requested_at=row["requested_at"],
            )
            db.session.add(appointment)
            appointments.append(appointment)
        db.session.commit()
        return [appointment.id for appointment in appointments]


def test_solicitudes_admin_muestra_pendientes_sin_asignacion_y_no_las_modifica(
    admin_auth,
):
    app, client, email, password, _administrator_id = admin_auth
    older = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    expected_ids = _add_requests(
        app,
        [
            {
                "name": "<script>alert(1)</script>",
                "email": "anterior@example.test",
                "requested_at": older,
                "modality": "in_person",
            },
            {
                "name": "Solicitud reciente A",
                "email": "reciente-a@example.test",
                "requested_at": newer,
                "modality": "virtual",
            },
            {
                "name": "Solicitud reciente B",
                "email": "reciente-b@example.test",
                "requested_at": newer,
                "modality": "hybrid",
                "status": "rejected",
            },
        ],
    )
    login_admin(client, email, password)

    response = client.get("/admin/appointments", base_url="https://localhost")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Servicio de solicitudes" in page
    assert "Pendiente" in page
    assert "Rechazada" in page
    assert "Presencial" in page
    assert "Virtual" in page
    assert "Híbrida" in page
    assert "20/11/2026 07:00" in page
    assert "Solicitud reciente A" in page
    assert "Solicitud reciente B" in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page
    assert page.index("reciente-b@example.test") < page.index("reciente-a@example.test")
    assert page.index("reciente-a@example.test") < page.index("anterior@example.test")

    with app.app_context():
        saved = db.session.query(Appointment).order_by(Appointment.id).all()
        assert [item.id for item in saved] == expected_ids
        assert all(item.professional_id is None for item in saved)
        assert all(item.availability_slot_id is None for item in saved)
        assert all(item.hold_expires_at is None for item in saved)
        assert [item.status for item in saved] == ["pending", "pending", "rejected"]


def test_solicitudes_admin_muestra_estado_vacio(admin_auth):
    _app, client, email, password, _administrator_id = admin_auth
    login_admin(client, email, password)

    response = client.get("/admin/appointments", base_url="https://localhost")

    assert response.status_code == 200
    assert "Todavía no hay solicitudes" in response.get_data(as_text=True)


def test_solicitudes_admin_requiere_autenticacion(app):
    app.config["SECRET_KEY"] = "clave-de-prueba"

    response = app.test_client().get("/admin/appointments", base_url="https://localhost")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")


def test_solicitudes_admin_pagina_resultados_y_pagina_siguiente(admin_auth):
    app, client, email, password, _administrator_id = admin_auth
    rows = [
        {
            "name": f"Solicitante {index:02d}",
            "email": f"solicitante-{index:02d}@example.test",
            "requested_at": datetime(2026, 11, 20, 12, index % 60, tzinfo=timezone.utc),
        }
        for index in range(21)
    ]
    _add_requests(app, rows)
    login_admin(client, email, password)

    first_page = client.get("/admin/appointments", base_url="https://localhost")
    second_page = client.get("/admin/appointments?page=2", base_url="https://localhost")

    assert "21 solicitudes" in first_page.get_data(as_text=True)
    assert "solicitante-00@example.test" not in first_page.get_data(as_text=True)
    assert "solicitante-00@example.test" in second_page.get_data(as_text=True)
    assert "Página 2 de 2" in second_page.get_data(as_text=True)


def test_dashboard_muestra_navegacion_y_conserva_logout_con_csrf(admin_auth):
    _app, client, email, password, _administrator_id = admin_auth
    login_admin(client, email, password)

    response = client.get("/admin/dashboard", base_url="https://localhost")
    page = response.get_data(as_text=True)

    for item in (
        "Resumen", "Solicitudes", "Profesionales", "Servicios", "Disponibilidad",
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
