"""Rutas de consulta limitada para las cuentas de psicólogos."""

from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    session,
    url_for,
)
from sqlalchemy import select

from .admin import (
    _ADMINISTRATOR_SESSION_KEY,
    _csrf_token,
    _valid_csrf_token,
)
from .extensions import db
from .models import Administrator, Appointment, AvailabilitySlot, Service


psychologist_bp = Blueprint("psychologist", __name__, url_prefix="/psychologist")
_LIMA = ZoneInfo("America/Lima")
_STATUS_LABELS = {
    "pending": "Pendiente",
    "confirmed": "Confirmada",
    "rejected": "Rechazada",
    "cancelled": "Cancelada",
    "expired": "Vencida",
}
_MODALITY_LABELS = {
    "in_person": "Presencial",
    "virtual": "Virtual",
    "hybrid": "Híbrida",
}


def censor_patient_name(first_name, first_surname, appointment_id):
    """Censura el apellido dejando solo el prefijo y nunca usa nombres históricos."""
    if not first_name or not first_surname:
        return f"Paciente · solicitud #{appointment_id}"
    visible_length = min(4, max(0, len(first_surname) - 1))
    visible = first_surname[:visible_length]
    masked = "°" * (len(first_surname) - visible_length)
    return f"{first_name} {visible}{masked}."


@psychologist_bp.before_request
def require_session_secret():
    if not current_app.config.get("SECRET_KEY"):
        abort(503, description="El acceso no está configurado.")


@psychologist_bp.context_processor
def inject_csrf_token():
    return {"csrf_token": _csrf_token()}


def psychologist_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        account_id = session.get(_ADMINISTRATOR_SESSION_KEY)
        if not isinstance(account_id, int):
            session.clear()
            return redirect(url_for("admin.login"))
        account = db.session.get(Administrator, account_id)
        if account is None or not account.is_active:
            session.clear()
            return redirect(url_for("admin.login"))
        if account.role != "psychologist" or account.professional_id is None:
            abort(403)
        g.account = account
        g.professional = account.professional
        return view(*args, **kwargs)

    return wrapped_view


@psychologist_bp.get("/appointments")
@psychologist_required
def appointments():
    rows = db.session.execute(
        select(
            Appointment.id,
            Service.name.label("service_name"),
            Appointment.status,
            Appointment.modality,
            Appointment.first_name,
            Appointment.first_surname,
            Appointment.availability_slot_id,
        )
        .join(Service, Service.id == Appointment.service_id)
        .where(Appointment.professional_id == g.professional.id)
        .order_by(Appointment.requested_at.desc(), Appointment.id.desc())
    ).all()

    slot_ids = [row.availability_slot_id for row in rows if row.availability_slot_id]
    slots_by_id = {}
    if slot_ids:
        slots_by_id = {
            slot.id: slot.starts_at
            for slot in db.session.scalars(
                select(AvailabilitySlot).where(AvailabilitySlot.id.in_(slot_ids))
            ).all()
        }

    appointments_view = []
    for row in rows:
        starts_at = slots_by_id.get(row.availability_slot_id)
        if starts_at is not None:
            if starts_at.tzinfo is None:
                # SQLite no conserva el offset de las citas creadas con la hora local.
                starts_at = starts_at.replace(tzinfo=_LIMA)
            starts_at = starts_at.astimezone(_LIMA)
        appointments_view.append(
            {
                "id": row.id,
                "service": row.service_name,
                "status": _STATUS_LABELS.get(row.status, row.status),
                "status_key": row.status,
                "modality": _MODALITY_LABELS.get(row.modality, row.modality),
                "patient": censor_patient_name(
                    row.first_name, row.first_surname, row.id
                ),
                "starts_at": starts_at,
            }
        )

    return render_template("psychologist_appointments.html", appointments=appointments_view)


@psychologist_bp.post("/logout")
@psychologist_required
def logout():
    if not _valid_csrf_token():
        abort(400, description="La solicitud no superó la validación de seguridad.")
    session.clear()
    flash("Sesión cerrada.", "success")
    return redirect(url_for("admin.login"))
