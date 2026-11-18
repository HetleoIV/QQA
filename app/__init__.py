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

    from .public import public_bp

    app.register_blueprint(public_bp)

    return app
