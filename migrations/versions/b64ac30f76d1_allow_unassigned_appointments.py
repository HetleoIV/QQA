"""Permite registrar solicitudes antes de asignar profesional y horario.

Revision ID: b64ac30f76d1
Revises: 934475c0b3e8
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa


revision = "b64ac30f76d1"
down_revision = "934475c0b3e8"
branch_labels = None
depends_on = None


ACTIVE_SLOT_PREDICATE = (
    "availability_slot_id IS NOT NULL AND status IN ('pending', 'confirmed')"
)


def upgrade():
    _upgrade_appointment_table()


def _upgrade_appointment_table():
    op.drop_index("uq_appointments_active_slot", table_name="appointments")

    with op.batch_alter_table("appointments") as batch_op:
        batch_op.drop_constraint(
            "ck_appointments_pending_has_expiration", type_="check"
        )
        batch_op.alter_column(
            "availability_slot_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.alter_column(
            "professional_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.create_check_constraint(
            "ck_appointments_assignment_pair",
            "(professional_id IS NULL AND availability_slot_id IS NULL) "
            "OR (professional_id IS NOT NULL AND availability_slot_id IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            "ck_appointments_confirmed_has_assignment",
            "status <> 'confirmed' OR "
            "(professional_id IS NOT NULL AND availability_slot_id IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            "ck_appointments_pending_assignment_expiration",
            "status <> 'pending' "
            "OR (availability_slot_id IS NULL AND hold_expires_at IS NULL) "
            "OR (availability_slot_id IS NOT NULL AND hold_expires_at IS NOT NULL)",
        )

    op.create_index(
        "uq_appointments_active_slot",
        "appointments",
        ["availability_slot_id"],
        unique=True,
        sqlite_where=sa.text(ACTIVE_SLOT_PREDICATE),
        postgresql_where=sa.text(ACTIVE_SLOT_PREDICATE),
    )


def downgrade():
    bind = op.get_bind()
    unassigned_count = bind.scalar(
        sa.text(
            "SELECT COUNT(*) FROM appointments "
            "WHERE professional_id IS NULL OR availability_slot_id IS NULL"
        )
    )
    if unassigned_count:
        raise RuntimeError(
            "No se puede revertir: existen solicitudes sin profesional u horario. "
            "Así se evita borrar o inventar asignaciones."
        )

    op.drop_index("uq_appointments_active_slot", table_name="appointments")
    with op.batch_alter_table("appointments") as batch_op:
        batch_op.drop_constraint(
            "ck_appointments_assignment_pair", type_="check"
        )
        batch_op.drop_constraint(
            "ck_appointments_confirmed_has_assignment", type_="check"
        )
        batch_op.drop_constraint(
            "ck_appointments_pending_assignment_expiration", type_="check"
        )
        batch_op.alter_column(
            "availability_slot_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.alter_column(
            "professional_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_appointments_pending_has_expiration",
            "status <> 'pending' OR hold_expires_at IS NOT NULL",
        )

    old_predicate = "status IN ('pending', 'confirmed')"
    op.create_index(
        "uq_appointments_active_slot",
        "appointments",
        ["availability_slot_id"],
        unique=True,
        sqlite_where=sa.text(old_predicate),
        postgresql_where=sa.text(old_predicate),
    )
