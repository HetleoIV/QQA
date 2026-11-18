"""Esquema inicial funcional de Q.A.A.

Revision ID: 934475c0b3e8
Revises:
Create Date: 2026-11-17

"""
from alembic import op
import sqlalchemy as sa


revision = "934475c0b3e8"
down_revision = None
branch_labels = None
depends_on = None


STATUSES = "'pending', 'confirmed', 'rejected', 'cancelled', 'expired'"


def upgrade():
    op.create_table(
        "administrators",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(email)) > 0", name="ck_administrators_email_not_empty"),
        sa.CheckConstraint("length(trim(password_hash)) > 0", name="ck_administrators_password_hash_not_empty"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "professionals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("photo_path", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(full_name)) > 0", name="ck_professionals_full_name_not_empty"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("display_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(slug)) > 0", name="ck_services_slug_not_empty"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_services_name_not_empty"),
        sa.CheckConstraint("display_order >= 0", name="ck_services_display_order_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("slug"),
    )
    services_seed = sa.table(
        "services",
        sa.column("slug", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("display_order", sa.SmallInteger()),
    )
    op.bulk_insert(
        services_seed,
        [
            {
                "slug": "psicologia-clinica",
                "name": "Psicología Clínica",
                "description": "Atención psicológica, evaluación, orientación y acompañamiento.",
                "display_order": 1,
            },
            {
                "slug": "psicologia-forense",
                "name": "Psicología Forense",
                "description": "Servicios psicológicos vinculados al ámbito judicial y legal.",
                "display_order": 2,
            },
            {
                "slug": "psicologia-organizacional",
                "name": "Psicología Organizacional",
                "description": "Bienestar, evaluación y orientación en contextos laborales e institucionales.",
                "display_order": 3,
            },
            {
                "slug": "asesoria-legal",
                "name": "Asesoría Legal",
                "description": "Orientación y asesoramiento jurídico según la competencia profesional.",
                "display_order": 4,
            },
            {
                "slug": "orientacion-nutricional",
                "name": "Orientación Nutricional",
                "description": "Orientación sobre hábitos alimenticios y bienestar nutricional.",
                "display_order": 5,
            },
        ],
    )
    op.create_table(
        "professional_services",
        sa.Column("professional_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["professional_id"], ["professionals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("professional_id", "service_id"),
    )
    op.create_index("ix_professional_services_service_id", "professional_services", ["service_id"])
    op.create_table(
        "availability_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("professional_id", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name="ck_availability_slots_positive_duration"),
        sa.ForeignKeyConstraint(["professional_id"], ["professionals.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "professional_id", name="uq_availability_slots_id_professional"),
        sa.UniqueConstraint("professional_id", "starts_at", "ends_at", name="uq_availability_slots_professional_interval"),
    )
    op.create_index(
        "ix_availability_slots_professional_start_enabled", "availability_slots",
        ["professional_id", "starts_at", "is_enabled"],
    )
    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("availability_slot_id", sa.Integer(), nullable=False),
        sa.Column("professional_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("modality", sa.String(length=20), nullable=False),
        sa.Column("contact_name", sa.String(length=120), nullable=False),
        sa.Column("contact_email", sa.String(length=254), nullable=False),
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
        sa.Column("public_ticket_hash", sa.String(length=64), nullable=False),
        sa.Column("cancellation_token_hash", sa.String(length=64), nullable=True),
        sa.Column("cancellation_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("hold_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_appointments_status_allowed"),
        sa.CheckConstraint("modality IN ('in_person', 'virtual', 'hybrid')", name="ck_appointments_modality_allowed"),
        sa.CheckConstraint("status <> 'pending' OR hold_expires_at IS NOT NULL", name="ck_appointments_pending_has_expiration"),
        sa.CheckConstraint("length(trim(contact_name)) > 0", name="ck_appointments_contact_name_not_empty"),
        sa.CheckConstraint("length(trim(contact_email)) > 0", name="ck_appointments_contact_email_not_empty"),
        sa.ForeignKeyConstraint(
            ["availability_slot_id", "professional_id"],
            ["availability_slots.id", "availability_slots.professional_id"],
            name="fk_appointments_slot_professional", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["professional_id", "service_id"],
            ["professional_services.professional_id", "professional_services.service_id"],
            name="fk_appointments_professional_service", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["professional_id"], ["professionals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_ticket_hash"),
        sa.UniqueConstraint("cancellation_token_hash"),
    )
    op.create_index(
        "uq_appointments_active_slot", "appointments", ["availability_slot_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('pending', 'confirmed')"),
        postgresql_where=sa.text("status IN ('pending', 'confirmed')"),
    )
    op.create_index("ix_appointments_status_requested_at", "appointments", ["status", "requested_at"])
    op.create_index("ix_appointments_service_status", "appointments", ["service_id", "status"])
    op.create_index("ix_appointments_hold_expiry", "appointments", ["status", "hold_expires_at"])
    op.create_table(
        "email_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=False),
        sa.Column("notification_type", sa.String(length=40), nullable=False),
        sa.Column("recipient_email", sa.String(length=254), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.SmallInteger(), server_default=sa.text("5"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "notification_type IN ('appointment_pending', 'appointment_confirmed', 'appointment_rejected', 'appointment_cancelled', 'appointment_expired')",
            name="ck_email_notifications_type_allowed",
        ),
        sa.CheckConstraint("status IN ('pending', 'sending', 'sent', 'failed')", name="ck_email_notifications_status_allowed"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_email_notifications_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_email_notifications_max_attempts_positive"),
        sa.CheckConstraint("length(trim(recipient_email)) > 0", name="ck_email_notifications_recipient_not_empty"),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_email_notifications_status_next_attempt", "email_notifications", ["status", "next_attempt_at"])
    op.create_index("ix_email_notifications_appointment_created", "email_notifications", ["appointment_id", "created_at"])
    op.create_table(
        "appointment_status_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=False),
        sa.Column("previous_status", sa.String(length=20), nullable=True),
        sa.Column("new_status", sa.String(length=20), nullable=False),
        sa.Column("changed_by_administrator_id", sa.Integer(), nullable=True),
        sa.Column("change_source", sa.String(length=20), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"previous_status IS NULL OR previous_status IN ({STATUSES})", name="ck_appointment_status_history_previous_allowed"),
        sa.CheckConstraint(f"new_status IN ({STATUSES})", name="ck_appointment_status_history_new_allowed"),
        sa.CheckConstraint("change_source IN ('administrator', 'system', 'public')", name="ck_appointment_status_history_source_allowed"),
        sa.CheckConstraint("previous_status IS NULL OR previous_status <> new_status", name="ck_appointment_status_history_status_changes"),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["changed_by_administrator_id"], ["administrators.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appointment_status_history_appointment_changed_at", "appointment_status_history", ["appointment_id", "changed_at"])
    op.create_index("ix_appointment_status_history_changed_by_admin", "appointment_status_history", ["changed_by_administrator_id"])

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("""
            CREATE TRIGGER trg_availability_slots_validate_insert
            BEFORE INSERT ON availability_slots
            BEGIN
                SELECT CASE WHEN strftime('%s', NEW.starts_at) IS NULL
                    OR strftime('%s', NEW.ends_at) IS NULL
                    OR ABS((julianday(NEW.ends_at) - julianday(NEW.starts_at)) * 86400 - 3600) > 0.001
                    THEN RAISE(ABORT, 'availability slot must be 60 minutes') END;
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM availability_slots AS existing
                    WHERE existing.professional_id = NEW.professional_id
                      AND julianday(NEW.starts_at) < julianday(existing.ends_at)
                      AND julianday(existing.starts_at) < julianday(NEW.ends_at)
                ) THEN RAISE(ABORT, 'availability slot overlaps another slot') END;
            END
        """)
        op.execute("""
            CREATE TRIGGER trg_availability_slots_validate_update
            BEFORE UPDATE OF professional_id, starts_at, ends_at ON availability_slots
            BEGIN
                SELECT CASE WHEN strftime('%s', NEW.starts_at) IS NULL
                    OR strftime('%s', NEW.ends_at) IS NULL
                    OR ABS((julianday(NEW.ends_at) - julianday(NEW.starts_at)) * 86400 - 3600) > 0.001
                    THEN RAISE(ABORT, 'availability slot must be 60 minutes') END;
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM availability_slots AS existing
                    WHERE existing.professional_id = NEW.professional_id
                      AND existing.id <> OLD.id
                      AND julianday(NEW.starts_at) < julianday(existing.ends_at)
                      AND julianday(existing.starts_at) < julianday(NEW.ends_at)
                ) THEN RAISE(ABORT, 'availability slot overlaps another slot') END;
            END
        """)
    elif dialect == "postgresql":
        op.execute("""
            CREATE FUNCTION prevent_availability_slot_overlap() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'UPDATE' THEN
                    PERFORM id FROM professionals
                    WHERE id IN (OLD.professional_id, NEW.professional_id)
                    ORDER BY id FOR UPDATE;
                ELSE
                    PERFORM id FROM professionals WHERE id = NEW.professional_id FOR UPDATE;
                END IF;

                IF EXTRACT(EPOCH FROM (NEW.ends_at - NEW.starts_at)) <> 3600 THEN
                    RAISE EXCEPTION 'availability slot must be 60 minutes'
                        USING ERRCODE = '23514', CONSTRAINT = 'ck_availability_slots_duration_60_minutes';
                END IF;

                IF EXISTS (
                    SELECT 1 FROM availability_slots AS existing
                    WHERE existing.professional_id = NEW.professional_id
                      AND (TG_OP <> 'UPDATE' OR existing.id <> NEW.id)
                      AND NEW.starts_at < existing.ends_at
                      AND existing.starts_at < NEW.ends_at
                ) THEN
                    RAISE EXCEPTION 'availability slot overlaps another slot'
                        USING ERRCODE = '23P01', CONSTRAINT = 'ex_availability_slots_no_overlap';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        op.execute("""
            CREATE TRIGGER trg_availability_slots_validate
            BEFORE INSERT OR UPDATE OF professional_id, starts_at, ends_at
            ON availability_slots
            FOR EACH ROW EXECUTE FUNCTION prevent_availability_slot_overlap();
        """)


def downgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_availability_slots_validate_update")
        op.execute("DROP TRIGGER IF EXISTS trg_availability_slots_validate_insert")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_availability_slots_validate ON availability_slots")
        op.execute("DROP FUNCTION IF EXISTS prevent_availability_slot_overlap()")

    op.drop_index("ix_appointment_status_history_changed_by_admin", table_name="appointment_status_history")
    op.drop_index("ix_appointment_status_history_appointment_changed_at", table_name="appointment_status_history")
    op.drop_table("appointment_status_history")
    op.drop_index("ix_email_notifications_appointment_created", table_name="email_notifications")
    op.drop_index("ix_email_notifications_status_next_attempt", table_name="email_notifications")
    op.drop_table("email_notifications")
    op.drop_index("ix_appointments_hold_expiry", table_name="appointments")
    op.drop_index("ix_appointments_service_status", table_name="appointments")
    op.drop_index("ix_appointments_status_requested_at", table_name="appointments")
    op.drop_index("uq_appointments_active_slot", table_name="appointments")
    op.drop_table("appointments")
    op.drop_index("ix_availability_slots_professional_start_enabled", table_name="availability_slots")
    op.drop_table("availability_slots")
    op.drop_index("ix_professional_services_service_id", table_name="professional_services")
    op.drop_table("professional_services")
    op.drop_table("services")
    op.drop_table("professionals")
    op.drop_table("administrators")
