import os
from datetime import timedelta

from sqlalchemy.engine import make_url


def normalize_database_url(database_url: str) -> str:
    """Fija Psycopg 3 para cualquier URL PostgreSQL recibida por entorno."""
    url = make_url(database_url)
    if url.get_backend_name() in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


class Config:
    """Configuración central de la aplicación, leída desde el entorno."""

    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = normalize_database_url(
        os.getenv("DATABASE_URL", "sqlite:///qaa.sqlite3")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    DEBUG = os.getenv("FLASK_DEBUG", "false").lower() in {"1", "true", "yes"}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = not DEBUG
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
    PENDING_HOLD_MINUTES = int(os.getenv("PENDING_HOLD_MINUTES", "15"))
