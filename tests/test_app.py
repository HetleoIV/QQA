import re

from app import create_app


def test_inicio_muestra_la_propuesta_y_los_servicios():
    app = create_app({"TESTING": True})
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Q.A.A. — Tu bienestar, nuestra prioridad" in page
    assert "Psicología clínica" in page
    assert "Asesoría legal" in page
    assert "Calle Callao N.º 162" in page
    assert "no se guarda ni confirma" in page


def test_recursos_de_la_interfaz_se_cargan():
    app = create_app({"TESTING": True})
    client = app.test_client()

    css_response = client.get("/static/css/styles.css")
    js_response = client.get("/static/js/main.js")
    image_response = client.get("/static/img/sala-referencial.jpg")

    assert css_response.status_code == 200
    assert js_response.status_code == 200
    assert image_response.status_code == 200


def test_navegacion_y_etiquetas_apuntan_a_elementos_existentes():
    app = create_app({"TESTING": True})
    page = app.test_client().get("/").get_data(as_text=True)

    element_ids = set(re.findall(r'\bid="([^"]+)"', page))
    anchor_targets = set(re.findall(r'href="#([^"]+)"', page))
    label_targets = set(re.findall(r'<label[^>]+for="([^"]+)"', page))

    assert anchor_targets <= element_ids
    assert label_targets <= element_ids
    assert len(element_ids) == len(re.findall(r'\bid="([^"]+)"', page))


def test_formulario_no_envia_ni_persiste_solicitudes():
    app = create_app({"TESTING": True})
    client = app.test_client()
    page = client.get("/").get_data(as_text=True)
    script = client.get("/static/js/main.js").get_data(as_text=True)

    assert 'id="demo-submit" type="button"' in page
    assert "No se enviaron ni guardaron datos" in script
    assert client.post("/").status_code == 405
