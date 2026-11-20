"""Modelos de datos de Q.A.A.; no contienen historias clínicas."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    and_,
    event,
    func,
    select,
    text,
    true,
)
from sqlalchemy.orm import Mapped, Session, foreign, mapped_column, relationship

from .extensions import db


APPOINTMENT_STATUSES = ("pending", "confirmed", "rejected", "cancelled", "expired")
APPOINTMENT_MODALITIES = ("in_person", "virtual", "hybrid")
NOTIFICATION_STATUSES = ("pending", "sending", "sent", "failed")
NOTIFICATION_TYPES = (
    "appointment_pending",
    "appointment_confirmed",
    "appointment_rejected",
    "appointment_cancelled",
    "appointment_expired",
)
HISTORY_SOURCES = ("administrator", "system", "public")
_status_sql = ", ".join(f"'{status}'" for status in APPOINTMENT_STATUSES)
_modality_sql = ", ".join(f"'{modality}'" for modality in APPOINTMENT_MODALITIES)
_notification_status_sql = ", ".join(
    f"'{status}'" for status in NOTIFICATION_STATUSES
)
_notification_type_sql = ", ".join(f"'{kind}'" for kind in NOTIFICATION_TYPES)
_history_source_sql = ", ".join(f"'{source}'" for source in HISTORY_SOURCES)


professional_services = db.Table(
    "professional_services",
    db.Column(
        "professional_id",
        db.Integer,
        db.ForeignKey("professionals.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    db.Column(
        "service_id",
        db.Integer,
        db.ForeignKey("services.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    db.Index("ix_professional_services_service_id", "service_id"),
)


class Administrator(db.Model):
    """Cuenta del único tipo de usuario administrativo del sistema."""

    __tablename__ = "administrators"
    __table_args__ = (
        CheckConstraint("length(trim(email)) > 0", name="ck_administrators_email_not_empty"),
        CheckConstraint(
            "length(trim(password_hash)) > 0",
            name="ck_administrators_password_hash_not_empty",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    status_changes: Mapped[list[AppointmentStatusHistory]] = relationship(
        back_populates="changed_by_administrator",
        foreign_keys="AppointmentStatusHistory.changed_by_administrator_id",
    )


class Service(db.Model):
    """Uno de los servicios institucionales publicados por Q.A.A."""

    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint("length(trim(slug)) > 0", name="ck_services_slug_not_empty"),
        CheckConstraint("length(trim(name)) > 0", name="ck_services_name_not_empty"),
        CheckConstraint("display_order >= 0", name="ck_services_display_order_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    display_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    professionals: Mapped[list[Professional]] = relationship(
        secondary=professional_services, back_populates="services"
    )
    appointments: Mapped[list[Appointment]] = relationship(back_populates="service")


class Professional(db.Model):
    """Perfil demostrativo de un profesional, sin credenciales inventadas."""

    __tablename__ = "professionals"
    __table_args__ = (
        CheckConstraint(
            "length(trim(full_name)) > 0", name="ck_professionals_full_name_not_empty"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    services: Mapped[list[Service]] = relationship(
        secondary=professional_services, back_populates="professionals"
    )
    availability_slots: Mapped[list[AvailabilitySlot]] = relationship(
        back_populates="professional"
    )
    appointments: Mapped[list[Appointment]] = relationship(
        back_populates="professional", foreign_keys="Appointment.professional_id"
    )


class AvailabilitySlot(db.Model):
    """Horario individual de 60 minutos asignado a un profesional."""

    __tablename__ = "availability_slots"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_availability_slots_positive_duration"),
        UniqueConstraint(
            "id", "professional_id", name="uq_availability_slots_id_professional"
        ),
        UniqueConstraint(
            "professional_id", "starts_at", "ends_at",
            name="uq_availability_slots_professional_interval",
        ),
        Index(
            "ix_availability_slots_professional_start_enabled",
            "professional_id", "starts_at", "is_enabled",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    professional_id: Mapped[int] = mapped_column(
        ForeignKey("professionals.id", ondelete="RESTRICT"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    professional: Mapped[Professional] = relationship(back_populates="availability_slots")
    appointments: Mapped[list[Appointment]] = relationship(
        back_populates="availability_slot",
        primaryjoin=lambda: and_(
            AvailabilitySlot.id == foreign(Appointment.availability_slot_id),
            AvailabilitySlot.professional_id == Appointment.professional_id,
        ),
        foreign_keys=lambda: [Appointment.availability_slot_id],
    )


class Appointment(db.Model):
    """Solicitud o reserva; los datos de contacto no equivalen a una cuenta."""

    __tablename__ = "appointments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["availability_slot_id", "professional_id"],
            ["availability_slots.id", "availability_slots.professional_id"],
            name="fk_appointments_slot_professional",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["professional_id", "service_id"],
            ["professional_services.professional_id", "professional_services.service_id"],
            name="fk_appointments_professional_service",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_status_sql})", name="ck_appointments_status_allowed"),
        CheckConstraint(
            f"modality IN ({_modality_sql})", name="ck_appointments_modality_allowed"
        ),
        CheckConstraint(
            "(professional_id IS NULL AND availability_slot_id IS NULL) "
            "OR (professional_id IS NOT NULL AND availability_slot_id IS NOT NULL)",
            name="ck_appointments_assignment_pair",
        ),
        CheckConstraint(
            "status <> 'confirmed' OR "
            "(professional_id IS NOT NULL AND availability_slot_id IS NOT NULL)",
            name="ck_appointments_confirmed_has_assignment",
        ),
        CheckConstraint(
            "status <> 'pending' "
            "OR (availability_slot_id IS NULL AND hold_expires_at IS NULL) "
            "OR (availability_slot_id IS NOT NULL AND hold_expires_at IS NOT NULL)",
            name="ck_appointments_pending_assignment_expiration",
        ),
        CheckConstraint(
            "length(trim(contact_name)) > 0", name="ck_appointments_contact_name_not_empty"
        ),
        CheckConstraint(
            "length(trim(contact_email)) > 0",
            name="ck_appointments_contact_email_not_empty",
        ),
        Index(
            "uq_appointments_active_slot", "availability_slot_id", unique=True,
            sqlite_where=text(
                "availability_slot_id IS NOT NULL AND status IN ('pending', 'confirmed')"
            ),
            postgresql_where=text(
                "availability_slot_id IS NOT NULL AND status IN ('pending', 'confirmed')"
            ),
        ),
        Index("ix_appointments_status_requested_at", "status", "requested_at"),
        Index("ix_appointments_service_status", "service_id", "status"),
        Index("ix_appointments_hold_expiry", "status", "hold_expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    availability_slot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    professional_id: Mapped[int | None] = mapped_column(
        ForeignKey("professionals.id", ondelete="RESTRICT"), nullable=True
    )
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    modality: Mapped[str] = mapped_column(String(20), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(120), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(254), nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    public_ticket_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    cancellation_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    cancellation_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    hold_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    availability_slot: Mapped[AvailabilitySlot | None] = relationship(
        back_populates="appointments",
        primaryjoin=lambda: and_(
            AvailabilitySlot.id == foreign(Appointment.availability_slot_id),
            AvailabilitySlot.professional_id == Appointment.professional_id,
        ),
        foreign_keys=lambda: [Appointment.availability_slot_id],
    )
    professional: Mapped[Professional | None] = relationship(
        back_populates="appointments", foreign_keys=[professional_id]
    )
    service: Mapped[Service] = relationship(back_populates="appointments")
    status_history: Mapped[list[AppointmentStatusHistory]] = relationship(
        back_populates="appointment", order_by="AppointmentStatusHistory.changed_at"
    )
    email_notifications: Mapped[list[EmailNotification]] = relationship(
        back_populates="appointment"
    )


class EmailNotification(db.Model):
    """Registro y cola persistente de notificaciones; no envía ni cifra correos."""

    __tablename__ = "email_notifications"
    __table_args__ = (
        CheckConstraint(
            f"notification_type IN ({_notification_type_sql})",
            name="ck_email_notifications_type_allowed",
        ),
        CheckConstraint(
            f"status IN ({_notification_status_sql})",
            name="ck_email_notifications_status_allowed",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_email_notifications_attempts_nonnegative"),
        CheckConstraint("max_attempts > 0", name="ck_email_notifications_max_attempts_positive"),
        CheckConstraint(
            "length(trim(recipient_email)) > 0",
            name="ck_email_notifications_recipient_not_empty",
        ),
        Index("ix_email_notifications_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_email_notifications_appointment_created", "appointment_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False
    )
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(254), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=5, server_default=text("5")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_payload: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    appointment: Mapped[Appointment] = relationship(back_populates="email_notifications")


class AppointmentStatusHistory(db.Model):
    """Auditoría de transiciones sin notas clínicas ni texto libre."""

    __tablename__ = "appointment_status_history"
    __table_args__ = (
        CheckConstraint(
            f"(previous_status IS NULL OR previous_status IN ({_status_sql}))",
            name="ck_appointment_status_history_previous_allowed",
        ),
        CheckConstraint(
            f"new_status IN ({_status_sql})",
            name="ck_appointment_status_history_new_allowed",
        ),
        CheckConstraint(
            f"change_source IN ({_history_source_sql})",
            name="ck_appointment_status_history_source_allowed",
        ),
        CheckConstraint(
            "previous_status IS NULL OR previous_status <> new_status",
            name="ck_appointment_status_history_status_changes",
        ),
        Index(
            "ix_appointment_status_history_appointment_changed_at",
            "appointment_id", "changed_at",
        ),
        Index(
            "ix_appointment_status_history_changed_by_admin", "changed_by_administrator_id"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False
    )
    previous_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_by_administrator_id: Mapped[int | None] = mapped_column(
        ForeignKey("administrators.id", ondelete="SET NULL"), nullable=True
    )
    change_source: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    appointment: Mapped[Appointment] = relationship(back_populates="status_history")
    changed_by_administrator: Mapped[Administrator | None] = relationship(
        back_populates="status_changes", foreign_keys=[changed_by_administrator_id]
    )


@event.listens_for(Session, "before_flush")
def validate_availability_slot_changes(session, _flush_context, _instances) -> None:
    """Valida duración y cruces antes de guardar slots mediante el ORM."""
    candidates = [
        slot
        for slot in session.new.union(session.dirty)
        if isinstance(slot, AvailabilitySlot)
        and slot not in session.deleted
        and (slot in session.new or session.is_modified(slot, include_collections=False))
    ]
    pending_candidates = []

    for slot in candidates:
        if slot.starts_at is None or slot.ends_at is None:
            continue  # NOT NULL rechazará el registro con un error de base de datos claro.
        if slot.ends_at - slot.starts_at != timedelta(hours=1):
            raise ValueError("Cada horario disponible debe durar exactamente 60 minutos.")

        professional_id = slot.professional_id
        if professional_id is None and slot.professional is not None:
            professional_id = slot.professional.id

        for other in pending_candidates:
            same_professional = (
                professional_id is not None and professional_id == other.professional_id
            ) or (slot.professional is not None and slot.professional is other.professional)
            if same_professional and (
                slot.starts_at < other.ends_at and other.starts_at < slot.ends_at
            ):
                raise ValueError("El profesional ya tiene un horario que se superpone.")

        if professional_id is not None:
            statement = select(AvailabilitySlot.id).where(
                AvailabilitySlot.professional_id == professional_id,
                AvailabilitySlot.starts_at < slot.ends_at,
                AvailabilitySlot.ends_at > slot.starts_at,
            )
            if slot.id is not None:
                statement = statement.where(AvailabilitySlot.id != slot.id)
            if session.execute(statement).first() is not None:
                raise ValueError("El profesional ya tiene un horario que se superpone.")

        pending_candidates.append(slot)
