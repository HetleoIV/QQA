import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

load_dotenv()

from .config import Config
from .extensions import db, migrate
from . import models as _models  # Registra todas las tablas en db.metadata.


def create_app(test_config: dict | None = None) -> Flask:
    """Crea y configura la aplicación Flask de Q.A.A."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)

    from .admin import admin_bp
    from .public import public_bp
    from .psychologist import psychologist_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(psychologist_bp)

    if os.environ.get("DATABASE_DIAGNOSTIC_TOKEN"):
        from .database_diagnostics import database_diagnostics_bp

        app.register_blueprint(database_diagnostics_bp)

    from .seed_data import register_seed_commands

    register_seed_commands(app)

    return app
