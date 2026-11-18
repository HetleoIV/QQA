import pytest

from app import create_app
from app.extensions import db


@pytest.fixture
def app(tmp_path):
    database_file = tmp_path / "qaa-test.sqlite3"
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_file.as_posix()}",
        }
    )

    with application.app_context():
        db.create_all()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()
