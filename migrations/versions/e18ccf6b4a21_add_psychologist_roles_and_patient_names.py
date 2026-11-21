"""Agrega roles de acceso y componentes de nombre para nuevas solicitudes.

Revision ID: e18ccf6b4a21
Revises: c2a8e6149f30
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa


revision = "e18ccf6b4a21"
down_revision = "c2a8e6149f30"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "administrators",
        sa.Column(
            "role", sa.String(length=20), nullable=False, server_default=sa.text("'admin'")
        ),
    )
    op.add_column(
        "administrators", sa.Column("professional_id", sa.Integer(), nullable=True)
    )
    with op.batch_alter_table("administrators") as batch_op:
        batch_op.create_foreign_key(
            "fk_administrators_professional_id",
            "professionals",
            ["professional_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "ck_administrators_role_allowed", "role IN ('admin', 'psychologist')"
        )
        batch_op.create_check_constraint(
            "ck_administrators_role_professional_link",
            "(role = 'admin' AND professional_id IS NULL) OR "
            "(role = 'psychologist' AND professional_id IS NOT NULL)",
        )
    op.create_index(
        "uq_administrators_professional_id",
        "administrators",
        ["professional_id"],
        unique=True,
        sqlite_where=sa.text("professional_id IS NOT NULL"),
        postgresql_where=sa.text("professional_id IS NOT NULL"),
    )

    for column_name in ("first_name", "second_name", "first_surname", "second_surname"):
        op.add_column(
            "appointments", sa.Column(column_name, sa.String(length=80), nullable=True)
        )


def downgrade():
    bind = op.get_bind()
    psychologist_accounts = bind.scalar(
        sa.text("SELECT COUNT(*) FROM administrators WHERE role = 'psychologist'")
    )
    if psychologist_accounts:
        raise RuntimeError(
            "No se puede revertir: existen cuentas de psicólogos. "
            "Revertir el rol permitiría que una versión antigua las trate como administradores."
        )

    structured_patients = bind.scalar(
        sa.text(
            "SELECT COUNT(*) FROM appointments WHERE first_name IS NOT NULL "
            "OR second_name IS NOT NULL OR first_surname IS NOT NULL "
            "OR second_surname IS NOT NULL"
        )
    )
    if structured_patients:
        raise RuntimeError(
            "No se puede revertir: existen nombres estructurados de pacientes. "
            "Así se evita perder esos datos."
        )

    for column_name in ("second_surname", "first_surname", "second_name", "first_name"):
        op.drop_column("appointments", column_name)

    op.drop_index("uq_administrators_professional_id", table_name="administrators")
    with op.batch_alter_table("administrators") as batch_op:
        batch_op.drop_constraint("ck_administrators_role_professional_link", type_="check")
        batch_op.drop_constraint("ck_administrators_role_allowed", type_="check")
        batch_op.drop_constraint("fk_administrators_professional_id", type_="foreignkey")
        batch_op.drop_column("professional_id")
        batch_op.drop_column("role")
