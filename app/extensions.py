import sqlite3

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine


# Las extensiones se conectan a la aplicación dentro de create_app().
db = SQLAlchemy()
migrate = Migrate()


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(connection, _connection_record) -> None:
    """Activa la aplicación de claves foráneas en las conexiones SQLite."""
    if isinstance(connection, sqlite3.Connection):
        connection.execute("PRAGMA foreign_keys=ON")
