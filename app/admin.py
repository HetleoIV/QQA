"""Autenticación y rutas provisionales del área administrativa."""

from datetime import date, datetime, time, timedelta, timezone
from contextlib import contextmanager
from functools import wraps
import hmac
import secrets
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.security import check_password_hash, generate_password_hash

from .admin_account_lock import lock_postgresql_admin_mutations
from .extensions import db
from .models import (
    Administrator,
    Appointment,
    AppointmentStatusHistory,
    AvailabilitySlot,
    Professional,
    Service,
)


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
_ADMINISTRATOR_SESSION_KEY = "administrator_id"
_CSRF_SESSION_KEY = "admin_csrf_token"
_INVALID_CREDENTIALS_MESSAGE = "Correo electrónico o contraseña incorrectos."
_APPOINTMENT_STATUS_LABELS = {
    "pending": "Pendiente",
    "confirmed": "Confirmada",
    "rejected": "Rechazada",
    "cancelled": "Cancelada",
    "expired": "Vencida",
}
_APPOINTMENT_MODALITY_LABELS = {
    "in_person": "Presencial",
    "virtual": "Virtual",
    "hybrid": "Híbrida",
}
_DASHBOARD_TIMEZONE = ZoneInfo("America/Lima")
_WEEKDAYS_ES = (
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"
)
_MONTHS_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
_MONTHS_SHORT_ES = (
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
)


def _requested_at_in_dashboard_timezone(requested_at: datetime) -> datetime:
    """Normaliza las fechas guardadas y las presenta en la zona horaria de Lima."""
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    return requested_at.astimezone(_DASHBOARD_TIMEZONE)


class _AccountActionError(Exception):
    """Error de negocio seguro para mostrar al administrar cuentas."""


class _ProfessionalActionError(Exception):
    """Error de negocio al gestionar profesionales."""


class _AppointmentActionError(Exception):
    """Error de negocio al revisar o asignar solicitudes."""


@contextmanager
def _administrator_write_transaction(actor_id: int):
    """Inicia una transacción de escritura con serialización según el motor."""
    db.session.rollback()  # cierra la transacción de lectura iniciada por admin_required
    try:
        dialect = db.engine.dialect.name
        if dialect == "postgresql":
            lock_postgresql_admin_mutations(db.session)
        elif dialect == "sqlite":
            db.session.execute(text("BEGIN IMMEDIATE"))
        else:
            raise _AccountActionError("El motor de base de datos no admite esta operación.")

        actor = db.session.scalar(
            select(Administrator)
            .where(Administrator.id == actor_id)
            .with_for_update()
        )
        if actor is None or not actor.is_active:
            raise _AccountActionError("La sesión administrativa ya no está activa.")

        yield actor
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def _active_administrator_count() -> int:
    return db.session.scalar(
        select(func.count(Administrator.id)).where(Administrator.is_active.is_(True))
    ) or 0


def _csrf_failure():
    abort(400, description="La solicitud no superó la validación de seguridad.")


def _show_account_action_error(error: Exception) -> None:
    if isinstance(error, _AccountActionError):
        flash(str(error), "error")
    elif isinstance(error, IntegrityError):
        flash("No se pudo guardar: revisa si el correo ya está registrado.", "error")
    elif isinstance(error, SQLAlchemyError):
        current_app.logger.error("Falló una operación de administración de cuentas.")
        flash("No se pudo completar la operación. Inténtalo nuevamente.", "error")
    else:
        raise error


def _valid_email(email: str) -> bool:
    if len(email) > 254 or email.count("@") != 1 or any(c.isspace() for c in email):
        return False
    local, domain = email.split("@", 1)
    return bool(local and domain and "." in domain and not domain.startswith("."))


def _render_professional_form(professional=None, *, selected_service_ids=(), status=200):
    services = db.session.scalars(
        select(Service).order_by(Service.display_order, Service.name)
    ).all()
    return render_template(
        "admin_professional_form.html",
        professional=professional,
        services=services,
        selected_service_ids=set(selected_service_ids),
        csrf_token=_csrf_token(),
    ), status


def _show_professional_action_error(error: Exception) -> None:
    if isinstance(error, _ProfessionalActionError):
        flash(str(error), "error")
    elif isinstance(error, IntegrityError):
        db.session.rollback()
        flash("No se pudo guardar: revisa si el correo está duplicado.", "error")
    elif isinstance(error, SQLAlchemyError):
        db.session.rollback()
        current_app.logger.error("Falló una operación de gestión de profesionales.")
        flash("No se pudo completar la operación. Inténtalo nuevamente.", "error")
    else:
        raise error


def _show_appointment_action_error(error: Exception) -> None:
    if isinstance(error, _AppointmentActionError):
        flash(str(error), "error")
    elif isinstance(error, (IntegrityError, ValueError)):
        db.session.rollback()
        flash(
            "No se pudo reservar ese horario porque dejó de estar disponible. "
            "Elige otra fecha u hora.",
            "error",
        )
    elif isinstance(error, SQLAlchemyError):
        db.session.rollback()
        current_app.logger.error("Falló una operación de asignación de solicitudes.")
        flash("No se pudo completar la operación. Inténtalo nuevamente.", "error")
    else:
        raise error


@admin_bp.before_request
def require_session_secret() -> None:
    """No habilita sesiones administrativas si falta la clave de firma."""
    if not current_app.config.get("SECRET_KEY"):
        current_app.logger.error(
            "La autenticación administrativa requiere configurar SECRET_KEY."
        )
        abort(503, description="El acceso administrativo no está configurado.")


def _csrf_token() -> str:
    token = session.get(_CSRF_SESSION_KEY)
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        session[_CSRF_SESSION_KEY] = token
    return token


@admin_bp.context_processor
def inject_admin_csrf_token():
    """Hace disponible el token en la plantilla base de todas las rutas admin."""
    return {"csrf_token": _csrf_token()}


def _valid_csrf_token() -> bool:
    expected = session.get(_CSRF_SESSION_KEY)
    submitted = request.form.get("csrf_token", "")
    return (
        isinstance(expected, str)
        and bool(submitted)
        and hmac.compare_digest(expected, submitted)
    )


def admin_required(view):
    """Exige un administrador activo cuya sesión siga siendo válida."""

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        administrator_id = session.get(_ADMINISTRATOR_SESSION_KEY)
        if not isinstance(administrator_id, int):
            session.clear()
            return redirect(url_for("admin.login"))

        administrator = db.session.get(Administrator, administrator_id)
        if administrator is None or not administrator.is_active:
            session.clear()
            return redirect(url_for("admin.login"))

        g.administrator = administrator
        return view(*args, **kwargs)

    return wrapped_view


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    """Valida credenciales y crea una sesión firmada para un admin activo."""
    if request.method == "POST":
        if not _valid_csrf_token():
            flash("La sesión del formulario expiró. Vuelve a intentarlo.", "error")
            return render_template("admin_login.html", csrf_token=_csrf_token()), 400

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        administrator = db.session.scalar(
            select(Administrator).where(
                func.lower(Administrator.email) == email,
                Administrator.is_active.is_(True),
            )
        )

        try:
            credentials_are_valid = (
                administrator is not None
                and check_password_hash(administrator.password_hash, password)
            )
        except (TypeError, ValueError):
            credentials_are_valid = False

        if not credentials_are_valid:
            flash(_INVALID_CREDENTIALS_MESSAGE, "error")
            return render_template("admin_login.html", csrf_token=_csrf_token())

        # Descarta el token previo antes de asociar la sesión al administrador.
        session.clear()
        session.permanent = True
        session[_ADMINISTRATOR_SESSION_KEY] = administrator.id
        session[_CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
        return redirect(url_for("admin.dashboard"))

    return render_template("admin_login.html", csrf_token=_csrf_token())


@admin_bp.get("/dashboard")
@admin_required
def dashboard():
    """Muestra indicadores reales de solicitudes y una agenda de demostración."""
    status_counts = dict(
        db.session.execute(
            select(Appointment.status, func.count(Appointment.id)).group_by(
                Appointment.status
            )
        ).all()
    )
    total_requests = sum(status_counts.values())
    now_in_peru = datetime.now(_DASHBOARD_TIMEZONE)
    today_in_peru = now_in_peru.date()
    current_date_label = (
        f"{_WEEKDAYS_ES[now_in_peru.weekday()]}, {today_in_peru.day} de "
        f"{_MONTHS_ES[today_in_peru.month - 1]} de {today_in_peru.year}"
    )

    upcoming_appointments = []
    demo_appointments = (
        (
            1,
            "09:00",
            "Solicitud de demostración 001",
            "Psicología Clínica",
            "confirmed",
            "Confirmada",
        ),
        (
            2,
            "11:30",
            "Solicitud de demostración 002",
            "Psicología Forense",
            "pending",
            "Pendiente",
        ),
        (
            4,
            "15:00",
            "Solicitud de demostración 003",
            "Psicología Organizacional",
            "confirmed",
            "Confirmada",
        ),
    )
    for days_ahead, time_label, reference, service, status_key, status_label in demo_appointments:
        appointment_date = today_in_peru + timedelta(days=days_ahead)
        upcoming_appointments.append(
            {
                "day": appointment_date.day,
                "month": _MONTHS_SHORT_ES[appointment_date.month - 1],
                "date_label": (
                    f"{appointment_date.day} de "
                    f"{_MONTHS_ES[appointment_date.month - 1]}"
                ),
                "time": time_label,
                "reference": reference,
                "service": service,
                "status_key": status_key,
                "status_label": status_label,
            }
        )

    metrics = (
        {
            "label": "Citas totales",
            "value": total_requests,
            "note": "Solicitudes recibidas",
            "icon": "calendar",
        },
        {
            "label": "Solicitudes pendientes",
            "value": status_counts.get("pending", 0),
            "note": "Requieren revisión administrativa",
            "icon": "pending",
        },
        {
            "label": "Citas confirmadas",
            "value": status_counts.get("confirmed", 0),
            "note": "Solicitudes confirmadas",
            "icon": "confirmed",
        },
        {
            "label": "Profesionales registrados",
            "value": 3,
            "note": "Perfiles de demostración",
            "icon": "team",
        },
    )
    recent_activity = (
        {"title": "Solicitud de demostración recibida", "time_label": "Hace 15 minutos"},
        {"title": "Cita de demostración confirmada", "time_label": "Hace 1 hora"},
        {"title": "Horario de demostración actualizado", "time_label": "Ayer"},
    )

    return render_template(
        "admin_dashboard.html",
        csrf_token=_csrf_token(),
        current_date_iso=today_in_peru.isoformat(),
        current_date_label=current_date_label,
        metrics=metrics,
        upcoming_appointments=upcoming_appointments,
        recent_activity=recent_activity,
    )


@admin_bp.get("/appointments")
@admin_required
def appointments():
    """Lista solicitudes sin exigir profesional ni horario asignado."""
    pagination = db.paginate(
        select(Appointment)
        .options(joinedload(Appointment.service))
        .order_by(Appointment.requested_at.desc(), Appointment.id.desc()),
        page=request.args.get("page", 1, type=int),
        per_page=20,
        error_out=False,
    )
    requested_at_by_id = {
        appointment.id: _requested_at_in_dashboard_timezone(appointment.requested_at)
        for appointment in pagination.items
    }
    return render_template(
        "admin_appointments.html",
        pagination=pagination,
        requested_at_by_id=requested_at_by_id,
        status_labels=_APPOINTMENT_STATUS_LABELS,
        modality_labels=_APPOINTMENT_MODALITY_LABELS,
    )


@admin_bp.get("/professionals")
@admin_required
def professionals():
    records = db.session.scalars(
        select(Professional)
        .options(selectinload(Professional.services))
        .order_by(Professional.full_name, Professional.id)
    ).all()
    return render_template("admin_professionals.html", professionals=records)


@admin_bp.route("/professionals/new", methods=["GET", "POST"])
@admin_required
def create_professional():
    if request.method == "GET":
        return _render_professional_form()
    if not _valid_csrf_token():
        _csrf_failure()

    full_name = " ".join(request.form.get("full_name", "").split())
    email = request.form.get("email", "").strip().lower()
    selected_ids = request.form.getlist("service_ids")
    try:
        service_ids = {int(value) for value in selected_ids}
    except ValueError:
        service_ids = set()
    error = None
    if not full_name or len(full_name) > 120:
        error = "Ingresa un nombre de hasta 120 caracteres."
    elif not _valid_email(email):
        error = "Ingresa un correo electrónico válido."
    elif not service_ids or len(service_ids) != len(selected_ids):
        error = "Selecciona al menos un servicio válido."

    if error:
        flash(error, "error")
        return _render_professional_form(
            selected_service_ids=service_ids, status=400
        )

    try:
        with _administrator_write_transaction(g.administrator.id):
            duplicate = db.session.scalar(
                select(Professional.id).where(func.lower(Professional.email) == email)
            )
            if duplicate is not None:
                raise _ProfessionalActionError("Ya existe un profesional con ese correo.")
            services = db.session.scalars(
                select(Service).where(
                    Service.id.in_(service_ids), Service.is_active.is_(True)
                )
            ).all()
            if len(services) != len(service_ids):
                raise _ProfessionalActionError("Selecciona únicamente servicios activos.")
            professional = Professional(
                full_name=full_name, email=email, is_active=True, services=services
            )
            db.session.add(professional)
            db.session.flush()
            professional_id = professional.id
    except (IntegrityError, SQLAlchemyError, _ProfessionalActionError) as error:
        _show_professional_action_error(error)
        return _render_professional_form(
            selected_service_ids=service_ids, status=400
        )

    flash("El profesional fue registrado.", "success")
    return redirect(url_for("admin.edit_professional", professional_id=professional_id))


@admin_bp.route("/professionals/<int:professional_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_professional(professional_id: int):
    professional = db.session.scalar(
        select(Professional)
        .options(selectinload(Professional.services))
        .where(Professional.id == professional_id)
    )
    if professional is None:
        abort(404)
    if request.method == "GET":
        return _render_professional_form(
            professional, selected_service_ids=[s.id for s in professional.services]
        )
    if not _valid_csrf_token():
        _csrf_failure()

    full_name = " ".join(request.form.get("full_name", "").split())
    email = request.form.get("email", "").strip().lower()
    selected_ids = request.form.getlist("service_ids")
    try:
        service_ids = {int(value) for value in selected_ids}
    except ValueError:
        service_ids = set()
    error = None
    if not full_name or len(full_name) > 120:
        error = "Ingresa un nombre de hasta 120 caracteres."
    elif not _valid_email(email):
        error = "Ingresa un correo electrónico válido."
    elif not service_ids or len(service_ids) != len(selected_ids):
        error = "Selecciona al menos un servicio válido."

    if error:
        flash(error, "error")
        return _render_professional_form(
            professional, selected_service_ids=service_ids, status=400
        )

    try:
        with _administrator_write_transaction(g.administrator.id):
            current = db.session.scalar(
                select(Professional)
                .where(Professional.id == professional_id)
                .with_for_update()
            )
            if current is None:
                raise _ProfessionalActionError("El profesional ya no existe.")
            duplicate = db.session.scalar(
                select(Professional.id).where(
                    func.lower(Professional.email) == email,
                    Professional.id != professional_id,
                )
            )
            if duplicate is not None:
                raise _ProfessionalActionError("Ya existe un profesional con ese correo.")
            current_service_ids = {service.id for service in current.services}
            services = db.session.scalars(
                select(Service).where(
                    Service.id.in_(service_ids),
                    (Service.is_active.is_(True) | Service.id.in_(current_service_ids)),
                )
            ).all()
            if len(services) != len(service_ids):
                raise _ProfessionalActionError("Selecciona servicios válidos.")
            current.full_name = full_name
            current.email = email
            current.services = services
    except (IntegrityError, SQLAlchemyError, _ProfessionalActionError) as error:
        _show_professional_action_error(error)
        db.session.expire_all()
        professional = db.session.scalar(
            select(Professional)
            .options(selectinload(Professional.services))
            .where(Professional.id == professional_id)
        )
        return _render_professional_form(
            professional, selected_service_ids=service_ids, status=400
        )

    flash("Los datos del profesional fueron actualizados.", "success")
    return redirect(url_for("admin.professionals"))


@admin_bp.post("/professionals/<int:professional_id>/activate")
@admin_required
def activate_professional(professional_id: int):
    if not _valid_csrf_token():
        _csrf_failure()
    try:
        with _administrator_write_transaction(g.administrator.id):
            professional = db.session.scalar(
                select(Professional)
                .where(Professional.id == professional_id)
                .with_for_update()
            )
            if professional is None:
                raise _ProfessionalActionError("El profesional indicado no existe.")
            professional.is_active = True
    except (IntegrityError, SQLAlchemyError, _ProfessionalActionError) as error:
        _show_professional_action_error(error)
    else:
        flash("El profesional fue activado.", "success")
    return redirect(url_for("admin.professionals"))


@admin_bp.post("/professionals/<int:professional_id>/deactivate")
@admin_required
def deactivate_professional(professional_id: int):
    if not _valid_csrf_token():
        _csrf_failure()
    try:
        with _administrator_write_transaction(g.administrator.id):
            professional = db.session.scalar(
                select(Professional)
                .where(Professional.id == professional_id)
                .with_for_update()
            )
            if professional is None:
                raise _ProfessionalActionError("El profesional indicado no existe.")
            professional.is_active = False
    except (IntegrityError, SQLAlchemyError, _ProfessionalActionError) as error:
        _show_professional_action_error(error)
    else:
        flash("El profesional fue desactivado. Su historial se conserva.", "success")
    return redirect(url_for("admin.professionals"))


def _appointment_detail(appointment_id: int):
    appointment = db.session.scalar(
        select(Appointment)
        .options(
            joinedload(Appointment.service),
            joinedload(Appointment.professional),
            joinedload(Appointment.availability_slot),
            selectinload(Appointment.status_history),
        )
        .where(Appointment.id == appointment_id)
    )
    if appointment is None:
        abort(404)
    professionals = db.session.scalars(
        select(Professional)
        .where(
            Professional.is_active.is_(True),
            Professional.services.any(Service.id == appointment.service_id),
        )
        .order_by(Professional.full_name)
    ).all()
    return render_template(
        "admin_appointment_detail.html",
        appointment=appointment,
        professionals=professionals,
        status_labels=_APPOINTMENT_STATUS_LABELS,
        modality_labels=_APPOINTMENT_MODALITY_LABELS,
        csrf_token=_csrf_token(),
    )


@admin_bp.get("/appointments/<int:appointment_id>")
@admin_required
def appointment_detail(appointment_id: int):
    return _appointment_detail(appointment_id)


@admin_bp.post("/appointments/<int:appointment_id>/assign")
@admin_required
def assign_appointment(appointment_id: int):
    if not _valid_csrf_token():
        _csrf_failure()
    try:
        professional_id = int(request.form.get("professional_id", ""))
        selected_date = date.fromisoformat(request.form.get("date", ""))
        selected_time = time.fromisoformat(request.form.get("time", ""))
    except ValueError:
        flash("Selecciona un profesional, una fecha y una hora válidos.", "error")
        return redirect(url_for("admin.appointment_detail", appointment_id=appointment_id))
    if selected_time.second or selected_time.microsecond:
        flash("La hora debe indicar minutos exactos.", "error")
        return redirect(url_for("admin.appointment_detail", appointment_id=appointment_id))

    starts_at = datetime.combine(selected_date, selected_time, tzinfo=_DASHBOARD_TIMEZONE)
    ends_at = starts_at + timedelta(hours=1)
    if starts_at <= datetime.now(_DASHBOARD_TIMEZONE):
        flash("La fecha y hora deben ser futuras.", "error")
        return redirect(url_for("admin.appointment_detail", appointment_id=appointment_id))

    try:
        with _administrator_write_transaction(g.administrator.id) as actor:
            appointment = db.session.scalar(
                select(Appointment)
                .where(Appointment.id == appointment_id)
                .with_for_update()
            )
            if appointment is None:
                raise _AppointmentActionError("La solicitud indicada no existe.")
            if appointment.status != "pending" or appointment.professional_id is not None:
                raise _AppointmentActionError("Esta solicitud ya no está pendiente de asignación.")
            professional = db.session.scalar(
                select(Professional)
                .where(
                    Professional.id == professional_id,
                    Professional.is_active.is_(True),
                    Professional.services.any(Service.id == appointment.service_id),
                )
                .with_for_update()
            )
            if professional is None:
                raise _AppointmentActionError(
                    "Selecciona un profesional activo que atienda el servicio solicitado."
                )

            slot = db.session.scalar(
                select(AvailabilitySlot)
                .where(
                    AvailabilitySlot.professional_id == professional_id,
                    AvailabilitySlot.starts_at == starts_at,
                    AvailabilitySlot.ends_at == ends_at,
                )
                .with_for_update()
            )
            if slot is not None and not slot.is_enabled:
                raise _AppointmentActionError("Ese horario no está habilitado.")
            if slot is None:
                slot = AvailabilitySlot(
                    professional_id=professional_id,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    is_enabled=True,
                )
                db.session.add(slot)
                # Ejecuta validación ORM y el trigger transaccional de solapamiento.
                db.session.flush()

            previous_status = appointment.status
            appointment.professional_id = professional_id
            appointment.availability_slot_id = slot.id
            appointment.status = "confirmed"
            appointment.hold_expires_at = None
            appointment.confirmed_at = datetime.now(timezone.utc)
            db.session.add(
                AppointmentStatusHistory(
                    appointment_id=appointment.id,
                    previous_status=previous_status,
                    new_status="confirmed",
                    changed_by_administrator_id=actor.id,
                    change_source="administrator",
                )
            )
    except (IntegrityError, SQLAlchemyError, ValueError, _AppointmentActionError) as error:
        _show_appointment_action_error(error)
        return redirect(url_for("admin.appointment_detail", appointment_id=appointment_id))

    flash("La cita fue asignada y confirmada.", "success")
    return redirect(url_for("admin.appointment_detail", appointment_id=appointment_id))


@admin_bp.post("/appointments/<int:appointment_id>/reject")
@admin_required
def reject_appointment(appointment_id: int):
    if not _valid_csrf_token():
        _csrf_failure()
    try:
        with _administrator_write_transaction(g.administrator.id) as actor:
            appointment = db.session.scalar(
                select(Appointment)
                .where(Appointment.id == appointment_id)
                .with_for_update()
            )
            if appointment is None:
                raise _AppointmentActionError("La solicitud indicada no existe.")
            if appointment.status != "pending":
                raise _AppointmentActionError("Solo se pueden rechazar solicitudes pendientes.")
            appointment.status = "rejected"
            appointment.hold_expires_at = None
            db.session.add(
                AppointmentStatusHistory(
                    appointment_id=appointment.id,
                    previous_status="pending",
                    new_status="rejected",
                    changed_by_administrator_id=actor.id,
                    change_source="administrator",
                )
            )
    except (IntegrityError, SQLAlchemyError, _AppointmentActionError) as error:
        _show_appointment_action_error(error)
        return redirect(url_for("admin.appointment_detail", appointment_id=appointment_id))

    flash("La solicitud fue rechazada.", "success")
    return redirect(url_for("admin.appointment_detail", appointment_id=appointment_id))


@admin_bp.post("/logout")
@admin_required
def logout():
    """Cierra la sesión mediante POST y exige el token CSRF."""
    if not _valid_csrf_token():
        abort(400, description="La solicitud no superó la validación de seguridad.")

    session.clear()
    flash("Sesión cerrada.", "success")
    return redirect(url_for("admin.login"))


@admin_bp.get("/users")
@admin_required
def users():
    administrators = db.session.scalars(
        select(Administrator).order_by(Administrator.created_at.desc(), Administrator.id)
    ).all()
    return render_template(
        "admin_users.html", administrators=administrators, csrf_token=_csrf_token()
    )


@admin_bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def create_user():
    if request.method == "POST":
        if not _valid_csrf_token():
            _csrf_failure()

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password_confirmation = request.form.get("password_confirmation", "")
        validation_error = None
        if (
            len(email) > 254
            or email.count("@") != 1
            or any(character.isspace() for character in email)
            or not email.split("@", 1)[0]
            or not email.split("@", 1)[1]
        ):
            validation_error = "Ingresa un correo electrónico válido."
        elif len(password) < 12:
            validation_error = "La contraseña debe tener al menos 12 caracteres."
        elif password != password_confirmation:
            validation_error = "Las contraseñas no coinciden."

        if validation_error:
            flash(validation_error, "error")
            return render_template(
                "admin_user_form.html", csrf_token=_csrf_token(), email=email
            ), 400

        try:
            with _administrator_write_transaction(g.administrator.id):
                duplicate = db.session.scalar(
                    select(Administrator.id).where(func.lower(Administrator.email) == email)
                )
                if duplicate is not None:
                    raise _AccountActionError("Ya existe una cuenta con ese correo electrónico.")
                administrator = Administrator(
                    email=email,
                    password_hash=generate_password_hash(password),
                    is_active=True,
                )
                db.session.add(administrator)
                db.session.flush()
                new_id = administrator.id
        except (IntegrityError, SQLAlchemyError, _AccountActionError) as error:
            _show_account_action_error(error)
            return render_template("admin_user_form.html", csrf_token=_csrf_token(), email=email), 400

        flash("La cuenta administrativa fue creada.", "success")
        return redirect(url_for("admin.user_detail", administrator_id=new_id))

    return render_template("admin_user_form.html", csrf_token=_csrf_token(), email="")


@admin_bp.get("/users/<int:administrator_id>")
@admin_required
def user_detail(administrator_id: int):
    administrator = db.session.get(Administrator, administrator_id)
    if administrator is None:
        abort(404)
    has_history = db.session.scalar(
        select(AppointmentStatusHistory.id)
        .where(AppointmentStatusHistory.changed_by_administrator_id == administrator_id)
        .limit(1)
    ) is not None
    return render_template(
        "admin_user_detail.html",
        administrator=administrator,
        has_history=has_history,
        is_current=administrator.id == g.administrator.id,
        active_count=_active_administrator_count(),
        csrf_token=_csrf_token(),
    )


@admin_bp.post("/users/<int:administrator_id>/activate")
@admin_required
def activate_user(administrator_id: int):
    if not _valid_csrf_token():
        _csrf_failure()
    try:
        with _administrator_write_transaction(g.administrator.id):
            administrator = db.session.scalar(
                select(Administrator)
                .where(Administrator.id == administrator_id)
                .with_for_update()
            )
            if administrator is None:
                raise _AccountActionError("La cuenta indicada no existe.")
            administrator.is_active = True
    except (IntegrityError, SQLAlchemyError, _AccountActionError) as error:
        _show_account_action_error(error)
    else:
        flash("La cuenta fue activada.", "success")
    return redirect(url_for("admin.user_detail", administrator_id=administrator_id))


@admin_bp.post("/users/<int:administrator_id>/deactivate")
@admin_required
def deactivate_user(administrator_id: int):
    if not _valid_csrf_token():
        _csrf_failure()
    try:
        with _administrator_write_transaction(g.administrator.id) as actor:
            if administrator_id == actor.id:
                raise _AccountActionError("No puedes desactivar tu propia cuenta.")
            administrator = db.session.scalar(
                select(Administrator)
                .where(Administrator.id == administrator_id)
                .with_for_update()
            )
            if administrator is None:
                raise _AccountActionError("La cuenta indicada no existe.")
            if administrator.is_active and _active_administrator_count() <= 1:
                raise _AccountActionError("No puedes desactivar la última cuenta activa.")
            administrator.is_active = False
    except (IntegrityError, SQLAlchemyError, _AccountActionError) as error:
        _show_account_action_error(error)
    else:
        flash("La cuenta fue desactivada.", "success")
    return redirect(url_for("admin.user_detail", administrator_id=administrator_id))


@admin_bp.get("/users/<int:administrator_id>/delete")
@admin_required
def confirm_delete_user(administrator_id: int):
    administrator = db.session.get(Administrator, administrator_id)
    if administrator is None:
        abort(404)
    has_history = db.session.scalar(
        select(AppointmentStatusHistory.id)
        .where(AppointmentStatusHistory.changed_by_administrator_id == administrator_id)
        .limit(1)
    ) is not None
    return render_template(
        "admin_user_delete_confirm.html",
        administrator=administrator,
        has_history=has_history,
        is_current=administrator.id == g.administrator.id,
        active_count=_active_administrator_count(),
        csrf_token=_csrf_token(),
    )


@admin_bp.post("/users/<int:administrator_id>/delete")
@admin_required
def delete_user(administrator_id: int):
    if not _valid_csrf_token():
        _csrf_failure()
    if request.form.get("confirm_delete") != "yes":
        flash("Confirma explícitamente la eliminación para continuar.", "error")
        return redirect(url_for("admin.confirm_delete_user", administrator_id=administrator_id))

    try:
        with _administrator_write_transaction(g.administrator.id) as actor:
            if administrator_id == actor.id:
                raise _AccountActionError("No puedes eliminar tu propia cuenta.")
            administrator = db.session.scalar(
                select(Administrator)
                .where(Administrator.id == administrator_id)
                .with_for_update()
            )
            if administrator is None:
                raise _AccountActionError("La cuenta indicada no existe.")
            if administrator.is_active and _active_administrator_count() <= 1:
                raise _AccountActionError("No puedes eliminar la última cuenta activa.")
            has_history = db.session.scalar(
                select(AppointmentStatusHistory.id)
                .where(
                    AppointmentStatusHistory.changed_by_administrator_id == administrator_id
                )
                .limit(1)
            ) is not None
            if has_history:
                raise _AccountActionError(
                    "Esta cuenta tiene historial de auditoría. Desactívala para conservar la atribución."
                )
            db.session.delete(administrator)
    except (IntegrityError, SQLAlchemyError, _AccountActionError) as error:
        _show_account_action_error(error)
        return redirect(url_for("admin.user_detail", administrator_id=administrator_id))

    flash("La cuenta fue eliminada.", "success")
    return redirect(url_for("admin.users"))
