"""Agrega correo de contacto a los perfiles profesionales.

Revision ID: c2a8e6149f30
Revises: b64ac30f76d1
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa


revision = "c2a8e6149f30"
down_revision = "b64ac30f76d1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "professionals",
        sa.Column("email", sa.String(length=254), nullable=True),
    )
    op.create_index(
        "uq_professionals_email_ci",
        "professionals",
        [sa.text("lower(email)")],
        unique=True,
        sqlite_where=sa.text("email IS NOT NULL"),
        postgresql_where=sa.text("email IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_professionals_email_ci", table_name="professionals")
    op.drop_column("professionals", "email")
