from flask import Blueprint, render_template


public_bp = Blueprint("public", __name__)


@public_bp.get("/")
def inicio():
    """Renderiza la página institucional pública de Q.A.A."""
    return render_template("index.html")
