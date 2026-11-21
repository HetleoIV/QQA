"""Autenticación y rutas provisionales del área administrativa."""

from datetime import datetime, timedelta, timezone
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
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash

from .admin_account_lock import lock_postgresql_admin_mutations
from .extensions import db
from .models import Administrator, Appointment, AppointmentStatusHistory


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
