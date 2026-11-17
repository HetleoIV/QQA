from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy


# Las extensiones se conectan a la aplicación dentro de create_app().
db = SQLAlchemy()
migrate = Migrate()
