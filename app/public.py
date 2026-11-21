import hashlib
import hmac
import logging
import re
import secrets

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.exc import SQLAlchemyError

from .extensions import db
from .models import APPOINTMENT_MODALITIES, Appointment, Service


public_bp = Blueprint("public", __name__)
logger = logging.getLogger(__name__)

_CSRF_SESSION_KEY = "public_appointment_csrf"
_MODALITY_LABELS = {
    "in_person": "Presencial",
    "virtual": "Virtual",
    "hybrid": "Híbrida",
}
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_PATTERN = re.compile(r"^[+()\d\s.-]{7,32}$")


def _csrf_token() -> str:
    token = session.get(_CSRF_SESSION_KEY)
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        session[_CSRF_SESSION_KEY] = token
    return token


def _render_home(services, *, csrf_token, form_data=None, errors=None, notice=None):
    return render_template(
        "index.html",
        services=services,
        modalities=_MODALITY_LABELS,
        csrf_token=csrf_token,
        form_data=form_data or {},
        errors=errors or {},
        form_notice=notice,
    )


@public_bp.route("/", methods=["GET", "POST"])
def inicio():
    """Muestra la página institucional y recibe solicitudes públicas sin asignar."""
    if not current_app.secret_key:
        return _render_home(
            [], csrf_token=None,
            notice="El formulario no está disponible temporalmente.",
        ), 503

    csrf_token = _csrf_token()
    form_data = {
        "service": request.form.get("service", ""),
        "modality": request.form.get("modality", ""),
        "name": request.form.get("name", ""),
        "email": request.form.get("email", ""),
        "phone": request.form.get("phone", ""),
        "consent": request.form.get("consent", ""),
    }
    errors = {}
    notice = None
    ticket = None
    response_status = 200
    services = []

    try:
        with db.session.begin():
            services = db.session.query(Service).filter_by(is_active=True).order_by(
                Service.display_order, Service.name
            ).all()

            if request.method == "POST":
                submitted_token = request.form.get("csrf_token", "")
                expected_token = session.get(_CSRF_SESSION_KEY, "")
                if not isinstance(expected_token, str) or not hmac.compare_digest(
                    expected_token, submitted_token
                ):
                    notice = "El formulario venció. Revisa los datos y vuelve a enviarlo."
                    csrf_token = secrets.token_urlsafe(32)
                    session[_CSRF_SESSION_KEY] = csrf_token
                    response_status = 400

                service_value = form_data["service"].strip()
                service = next(
                    (item for item in services if str(item.id) == service_value), None
                )
                if not service:
                    errors["service"] = "Selecciona un servicio disponible."

                modality = form_data["modality"].strip()
                if modality not in APPOINTMENT_MODALITIES:
                    errors["modality"] = "Selecciona una modalidad válida."

                contact_name = form_data["name"].strip()
                if not contact_name:
                    errors["name"] = "Escribe tu nombre completo."
                elif len(contact_name) > 120:
                    errors["name"] = "El nombre no puede superar 120 caracteres."

                contact_email = form_data["email"].strip()
                if not contact_email:
                    errors["email"] = "Escribe tu correo electrónico."
                elif len(contact_email) > 254 or not _EMAIL_PATTERN.fullmatch(contact_email):
                    errors["email"] = "Escribe un correo electrónico válido."

                contact_phone = form_data["phone"].strip()
                phone_digits = re.sub(r"\D", "", contact_phone)
                if (
                    not _PHONE_PATTERN.fullmatch(contact_phone)
                    or not 7 <= len(phone_digits) <= 15
                ):
                    errors["phone"] = "Escribe un teléfono válido con código de país si corresponde."

                if form_data["consent"] != "yes":
                    errors["consent"] = "Debes aceptar el aviso de privacidad para continuar."

                if notice:
                    errors["form"] = notice
                if errors:
                    response_status = 400
                elif not errors:
                    ticket = secrets.token_urlsafe(24)
                    ticket_hash = hashlib.sha256(ticket.encode("utf-8")).hexdigest()
                    db.session.add(
                        Appointment(
                            service_id=service.id,
                            modality=modality,
                            contact_name=contact_name,
                            contact_email=contact_email,
                            contact_phone=contact_phone,
                            public_ticket_hash=ticket_hash,
                            status="pending",
                            professional_id=None,
                            availability_slot_id=None,
                            hold_expires_at=None,
                        )
                    )
                    session[_CSRF_SESSION_KEY] = secrets.token_urlsafe(32)

        if ticket:
            flash(ticket, "appointment_ticket")
            return redirect(url_for("public.inicio"), code=303)
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("No se pudo registrar la solicitud pública de cita.")
        notice = "No pudimos guardar tu solicitud. Inténtalo nuevamente más tarde."
        response_status = 503

    return _render_home(
        services,
        csrf_token=csrf_token,
        form_data=form_data,
        errors=errors,
        notice=notice,
    ), response_status
