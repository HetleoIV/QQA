"""Autenticación y rutas provisionales del área administrativa."""

from functools import wraps
import hmac
import secrets

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
from sqlalchemy import func, select
from werkzeug.security import check_password_hash

from .extensions import db
from .models import Administrator


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
_ADMINISTRATOR_SESSION_KEY = "administrator_id"
_CSRF_SESSION_KEY = "admin_csrf_token"
_INVALID_CREDENTIALS_MESSAGE = "Correo electrónico o contraseña incorrectos."


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
    """Página provisional protegida; aún no contiene gestión administrativa."""
    return render_template("admin_dashboard.html", csrf_token=_csrf_token())


@admin_bp.post("/logout")
@admin_required
def logout():
    """Cierra la sesión mediante POST y exige el token CSRF."""
    if not _valid_csrf_token():
        abort(400, description="La solicitud no superó la validación de seguridad.")

    session.clear()
    flash("Sesión cerrada.", "success")
    return redirect(url_for("admin.login"))
