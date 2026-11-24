import hashlib
import logging
import re

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from app import create_app
from app.extensions import db
from app.models import Appointment, Service


@pytest.fixture
def public_app(tmp_path):
    database_file = tmp_path / "qaa-public-form.sqlite3"
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "clave-de-prueba-no-usar-en-produccion",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_file.as_posix()}",
        }
    )
    with app.app_context():
        db.create_all()
        db.session.add(
            Service(
                slug="servicio-prueba",
                name="Servicio de prueba",
                description="Servicio ficticio para pruebas.",
                display_order=1,
            )
        )
        db.session.add(
            Service(
                slug="servicio-inactivo",
                name="Servicio inactivo",
                description="No debe aparecer en solicitudes públicas.",
                is_active=False,
            )
        )
        db.session.commit()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


def _service_id(app):
    with app.app_context():
        return db.session.query(Service).filter_by(slug="servicio-prueba").one().id


def _csrf_token(client):
    page = client.get("/").get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match
    return match.group(1)


def _valid_form(client, service_id, **changes):
    form = {
        "csrf_token": _csrf_token(client),
        "service": str(service_id),
        "modality": "virtual",
        "first_name": "Persona",
        "second_name": "",
        "first_surname": "de",
        "second_surname": "prueba",
        "email": "persona@example.test",
        "phone": "987654321",
        "consent": "yes",
    }
    form.update(changes)
    return form


def _ticket_from(page):
    match = re.search(r'class="booking-ticket">Ticket <strong>([^<]+)</strong>', page)
    assert match
    return match.group(1)


def test_inicio_muestra_servicios_y_formulario_publico(public_app):
    client = public_app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "SUNQU — Consultoría Integral" in page
    assert 'src="/static/img/sunqu-logo.png"' in page
    assert "sala-referencial.jpg" not in page
    assert "Psicología clínica" in page
    assert "Asesoría legal" in page
    assert "Servicio de prueba" in page
    assert "Servicio inactivo" not in page
    assert '<input type="hidden" name="modality" value="virtual">' in page
    assert '<select id="modality"' not in page
    assert "Virtual" in page
    assert "Modalidad de atención" in page
    assert "Presencial" not in page
    assert "Híbrida" not in page
    assert "aviso de privacidad" in page
    assert "Atención exclusivamente virtual" in page


def test_pagina_con_token_csrf_no_se_guarda_en_cache(public_app):
    response = public_app.test_client().get("/")

    assert response.status_code == 200
    assert response.cache_control.no_store
    assert response.cache_control.private
    assert "Cookie" in response.vary


def test_recursos_de_la_interfaz_se_cargan(public_app):
    client = public_app.test_client()

    css_response = client.get("/static/css/styles.css")
    js_response = client.get("/static/js/main.js")
    image_response = client.get("/static/img/sala-referencial.jpg")
    sunqu_image_response = client.get("/static/img/sunqu-logo.png")

    assert css_response.status_code == 200
    assert js_response.status_code == 200
    assert image_response.status_code == 200
    assert sunqu_image_response.status_code == 200


def test_navegacion_y_etiquetas_apuntan_a_elementos_existentes(public_app):
    page = public_app.test_client().get("/").get_data(as_text=True)

    element_ids = set(re.findall(r'\bid="([^"]+)"', page))
    anchor_targets = set(re.findall(r'href="#([^"]+)"', page))
    label_targets = set(re.findall(r'<label[^>]+for="([^"]+)"', page))

    assert anchor_targets <= element_ids
    assert label_targets <= element_ids
    assert len(element_ids) == len(re.findall(r'\bid="([^"]+)"', page))


def test_solicitud_valida_guarda_cita_pendiente_sin_asignacion_y_muestra_ticket(
    public_app,
):
    client = public_app.test_client()
    service_id = _service_id(public_app)

    response = client.post(
        "/", data=_valid_form(client, service_id), follow_redirects=True
    )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Hemos recibido tu solicitud." in page
    assert "Conserva este número de referencia" in page
    ticket = _ticket_from(page)
    assert len(ticket) >= 24

    with public_app.app_context():
        appointment = db.session.query(Appointment).one()
        assert appointment.service_id == service_id
        assert appointment.modality == "virtual"
        assert appointment.contact_name == "Persona de prueba"
        assert appointment.first_name == "Persona"
        assert appointment.second_name is None
        assert appointment.first_surname == "de"
        assert appointment.second_surname == "prueba"
        assert appointment.contact_email == "persona@example.test"
        assert appointment.contact_phone == "987654321"
        assert appointment.status == "pending"
        assert appointment.professional_id is None
        assert appointment.availability_slot_id is None
        assert appointment.hold_expires_at is None
        assert appointment.public_ticket_hash == hashlib.sha256(
            ticket.encode("utf-8")
        ).hexdigest()
        assert ticket not in appointment.public_ticket_hash


@pytest.mark.parametrize(
    ("field", "error_id"),
    [
        ("service", "service-error"),
        ("modality", "modality-error"),
        ("first_name", "first-name-error"),
        ("first_surname", "first-surname-error"),
        ("second_surname", "second-surname-error"),
        ("email", "email-error"),
        ("phone", "phone-error"),
        ("consent", "consent-error"),
    ],
)
def test_campos_obligatorios_se_validan_en_el_servidor(
    public_app, field, error_id
):
    client = public_app.test_client()
    form = _valid_form(client, _service_id(public_app))
    form.pop(field)

    response = client.post("/", data=form)

    assert response.status_code == 400
    assert f'id="{error_id}"' in response.get_data(as_text=True)
    with public_app.app_context():
        assert db.session.query(Appointment).count() == 0


def test_segundo_nombre_es_opcional_y_componentes_se_conservan(public_app):
    client = public_app.test_client()
    form = _valid_form(client, _service_id(public_app))
    form["second_name"] = "Fernanda"
    response = client.post("/", data=form, follow_redirects=True)
    assert response.status_code == 200
    with public_app.app_context():
        appointment = db.session.query(Appointment).one()
        assert appointment.contact_name == "Persona Fernanda de prueba"
        assert appointment.second_name == "Fernanda"


@pytest.mark.parametrize("service_value", ["99999", "inactive"])
def test_servicio_inexistente_o_inactivo_es_rechazado(public_app, service_value):
    client = public_app.test_client()
    inactive_id = None
    if service_value == "inactive":
        with public_app.app_context():
            inactive_id = db.session.query(Service).filter_by(
                slug="servicio-inactivo"
            ).one().id
    form = _valid_form(
        client,
        _service_id(public_app),
        service=inactive_id if inactive_id is not None else service_value,
    )

    response = client.post("/", data=form)

    assert response.status_code == 400
    assert 'id="service-error"' in response.get_data(as_text=True)
    with public_app.app_context():
        assert db.session.query(Appointment).count() == 0


@pytest.mark.parametrize("modality", ["in_person", "hybrid", "a domicilio"])
def test_modalidad_no_virtual_es_rechazada(public_app, modality):
    client = public_app.test_client()
    response = client.post(
        "/",
        data=_valid_form(client, _service_id(public_app), modality=modality),
    )

    assert response.status_code == 400
    assert 'id="modality-error"' in response.get_data(as_text=True)
    with public_app.app_context():
        assert db.session.query(Appointment).count() == 0


@pytest.mark.parametrize(
    ("field", "value", "error_id"),
    [
        ("email", "no-es-correo", "email-error"),
        ("phone", "solo letras", "phone-error"),
    ],
)
def test_datos_de_contacto_con_formato_invalido_se_rechazan(
    public_app, field, value, error_id
):
    client = public_app.test_client()
    response = client.post(
        "/", data=_valid_form(client, _service_id(public_app), **{field: value})
    )

    assert response.status_code == 400
    assert f'id="{error_id}"' in response.get_data(as_text=True)
    with public_app.app_context():
        assert db.session.query(Appointment).count() == 0


def test_ticket_es_unico_y_token_de_formulario_impide_reenvio(public_app):
    client = public_app.test_client()
    service_id = _service_id(public_app)
    first_form = _valid_form(client, service_id)

    first_response = client.post("/", data=first_form, follow_redirects=True)
    first_ticket = _ticket_from(first_response.get_data(as_text=True))
    replay = client.post("/", data=first_form)

    assert replay.status_code == 400
    second_response = client.post(
        "/", data=_valid_form(client, service_id), follow_redirects=True
    )
    second_ticket = _ticket_from(second_response.get_data(as_text=True))
    assert first_ticket != second_ticket

    with public_app.app_context():
        appointments = db.session.query(Appointment).all()
        assert len(appointments) == 2
        assert len({item.public_ticket_hash for item in appointments}) == 2


def test_csrf_invalido_registra_solo_indicadores_seguros(public_app, caplog):
    client = public_app.test_client()
    expected_token = _csrf_token(client)
    submitted_token = "token-de-prueba-invalido"
    caplog.set_level(logging.WARNING, logger="app.public")

    response = client.post(
        "/",
        data=_valid_form(
            client,
            _service_id(public_app),
            csrf_token=submitted_token,
        ),
    )

    assert response.status_code == 400
    assert "Cache-Control" in response.headers
    record = next(
        record for record in caplog.records
        if "Rechazo CSRF del formulario público" in record.getMessage()
    )
    message = record.getMessage()
    assert "session_cookie_present=True" in message
    assert "expected_token_present=True" in message
    assert "submitted_token_present=True" in message
    assert "tokens_match=False" in message
    assert response.cache_control.no_store
    assert response.cache_control.private
    assert expected_token not in message
    assert submitted_token not in message


def test_csrf_ausente_se_rechaza_y_registra_cookie_ausente(public_app, caplog):
    client = public_app.test_client()
    caplog.set_level(logging.WARNING, logger="app.public")
    service_id = _service_id(public_app)
    response = client.post(
        "/",
        data={
            "service": str(service_id),
            "modality": "virtual",
            "first_name": "Persona",
            "first_surname": "de",
            "second_surname": "prueba",
            "email": "persona@example.test",
            "phone": "987654321",
            "consent": "yes",
        },
    )

    assert response.status_code == 400
    assert "form-alert" in response.get_data(as_text=True)
    assert response.cache_control.no_store
    record = next(
        record for record in caplog.records
        if "Rechazo CSRF del formulario público" in record.getMessage()
    )
    message = record.getMessage()
    assert "session_cookie_present=False" in message
    assert "expected_token_present=True" in message
    assert "submitted_token_present=False" in message
    assert "tokens_match=False" in message
    with public_app.app_context():
        assert db.session.query(Appointment).count() == 0


def test_error_de_base_de_datos_no_expone_detalles_al_visitante(public_app):
    client = public_app.test_client()
    service_id = _service_id(public_app)
    internal_detail = "detalle interno que no debe aparecer"

    def fail_appointment_insert(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        if "INSERT INTO appointments" in statement:
            raise SQLAlchemyError(internal_detail)

    with public_app.app_context():
        engine = db.engine
        event.listen(engine, "before_cursor_execute", fail_appointment_insert)
        try:
            response = client.post(
                "/", data=_valid_form(client, service_id), follow_redirects=True
            )
        finally:
            event.remove(engine, "before_cursor_execute", fail_appointment_insert)

    page = response.get_data(as_text=True)
    assert response.status_code == 503
    assert "No pudimos guardar tu solicitud" in page
    assert internal_detail not in page
    with public_app.app_context():
        assert db.session.query(Appointment).count() == 0


def test_acceso_administrativo_esta_en_el_pie_fuera_de_la_navegacion(public_app):
    client = public_app.test_client()

    homepage = client.get("/").get_data(as_text=True)
    assert 'href="/admin/dashboard"' in homepage
    assert "Acceso administrativo" in homepage
    navigation = homepage.split('<nav class="site-nav"', 1)[1].split("</nav>", 1)[0]
    assert "Acceso administrativo" not in navigation
