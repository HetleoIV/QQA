from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.config import normalize_database_url
from app.extensions import db
from app.models import (
    Administrator,
    Appointment,
    AppointmentStatusHistory,
    AvailabilitySlot,
    EmailNotification,
    Professional,
    Service,
)


def add_booking_graph(app, *, status="pending"):
    now = datetime.now(timezone.utc)
    with app.app_context():
        admin = Administrator(email="admin@example.test", password_hash="hash-ficticio")
        service = Service(
            slug="psicologia-clinica", name="Psicología Clínica",
            description="Servicio demostrativo.", display_order=1,
        )
        professional = Professional(full_name="Profesional ficticio", services=[service])
        db.session.add_all([admin, service, professional])
        db.session.flush()

        slot = AvailabilitySlot(
            professional=professional,
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, hours=1),
        )
        db.session.add(slot)
        db.session.flush()

        appointment = Appointment(
            availability_slot=slot,
            professional=professional,
            service=service,
            modality="virtual",
            contact_name="Persona de prueba",
            contact_email="persona@example.test",
            public_ticket_hash="a" * 64,
            status=status,
            hold_expires_at=(now + timedelta(minutes=15) if status == "pending" else None),
        )
        history = AppointmentStatusHistory(
            appointment=appointment,
            previous_status=None,
            new_status=status,
            change_source="system",
            changed_by_administrator=admin,
        )
        notification = EmailNotification(
            appointment=appointment,
            notification_type="appointment_pending",
            recipient_email="persona@example.test",
            status="pending",
            idempotency_key="appointment-1-pending",
            encrypted_payload=None,
        )
        db.session.add_all([appointment, history, notification])
        db.session.commit()
        return appointment.id, slot.id, professional.id, service.id


def test_metadata_contiene_las_ocho_tablas(app):
    expected = {
        "administrators", "professionals", "services", "professional_services",
        "availability_slots", "appointments", "email_notifications",
        "appointment_status_history",
    }
    with app.app_context():
        assert set(db.metadata.tables) == expected
        assert set(inspect(db.engine).get_table_names()) == expected


def test_relaciones_profesional_servicio_cita_historial_y_correo(app):
    appointment_id, _, _, _ = add_booking_graph(app)
    with app.app_context():
        appointment = db.session.get(Appointment, appointment_id)
        assert appointment is not None
        assert appointment.professional.full_name == "Profesional ficticio"
        assert appointment.service.slug == "psicologia-clinica"
        assert appointment.availability_slot.professional_id == appointment.professional_id
        assert appointment.professional.services == [appointment.service]
        assert appointment.status_history[0].new_status == "pending"
        assert appointment.status_history[0].change_source == "system"
        assert appointment.email_notifications[0].status == "pending"
        assert appointment.email_notifications[0].encrypted_payload is None


def test_indice_unico_impide_reservas_activas_y_estado_terminal_libera_horario(app):
    first_id, slot_id, professional_id, service_id = add_booking_graph(app)
    with app.app_context():
        duplicate = Appointment(
            availability_slot_id=slot_id,
            professional_id=professional_id,
            service_id=service_id,
            modality="in_person",
            contact_name="Otra persona ficticia",
            contact_email="otra@example.test",
            public_ticket_hash="b" * 64,
            status="confirmed",
        )
        db.session.add(duplicate)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

        first = db.session.get(Appointment, first_id)
        first.status = "expired"
        db.session.flush()
        replacement = Appointment(
            availability_slot_id=slot_id,
            professional_id=professional_id,
            service_id=service_id,
            modality="hybrid",
            contact_name="Tercera persona ficticia",
            contact_email="tercera@example.test",
            public_ticket_hash="c" * 64,
            status="pending",
            hold_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        db.session.add(replacement)
        db.session.commit()
        active = db.session.scalars(
            select(Appointment.status).where(
                Appointment.availability_slot_id == slot_id,
                Appointment.status.in_(("pending", "confirmed")),
            )
        ).all()
        assert active == ["pending"]


def test_claves_compuestas_impiden_slot_o_servicio_incompatibles(app):
    with app.app_context():
        service_a = Service(slug="legal", name="Asesoría Legal", description="Ficticio")
        service_b = Service(slug="nutricion", name="Orientación Nutricional", description="Ficticio")
        professional_a = Professional(full_name="Profesional A", services=[service_a])
        professional_b = Professional(full_name="Profesional B", services=[service_b])
        now = datetime.now(timezone.utc) + timedelta(days=1)
        slot = AvailabilitySlot(
            professional=professional_a, starts_at=now, ends_at=now + timedelta(hours=1)
        )
        db.session.add_all([service_a, service_b, professional_a, professional_b, slot])
        db.session.flush()
        bad_appointment = Appointment(
            availability_slot_id=slot.id,
            professional_id=professional_b.id,
            service_id=service_b.id,
            modality="virtual",
            contact_name="Persona de prueba",
            contact_email="contacto@example.test",
            public_ticket_hash="d" * 64,
            status="pending",
            hold_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        db.session.add(bad_appointment)
        with pytest.raises(IntegrityError):
            db.session.flush()


def test_slots_orm_solo_acepta_horas_completas_y_sin_solapamiento(app):
    with app.app_context():
        professional = Professional(full_name="Profesional ficticio")
        start = datetime(2030, 1, 1, 9, tzinfo=timezone.utc)
        first = AvailabilitySlot(
            professional=professional, starts_at=start, ends_at=start + timedelta(hours=1)
        )
        db.session.add_all([professional, first])
        db.session.flush()

        overlap = AvailabilitySlot(
            professional=professional,
            starts_at=start + timedelta(minutes=30),
            ends_at=start + timedelta(hours=1, minutes=30),
        )
        db.session.add(overlap)
        with pytest.raises(ValueError, match="superpone"):
            db.session.flush()
        db.session.rollback()

        short_slot = AvailabilitySlot(
            professional=professional,
            starts_at=start + timedelta(hours=1),
            ends_at=start + timedelta(hours=1, minutes=45),
        )
        db.session.add(short_slot)
        with pytest.raises(ValueError, match="60 minutos"):
            db.session.flush()


def test_pendiente_requiere_expiracion_y_hashes_publicos_son_unicos(app):
    with app.app_context():
        service = Service(slug="clinica", name="Psicología Clínica", description="Ficticio")
        professional = Professional(full_name="Profesional de prueba", services=[service])
        start = datetime.now(timezone.utc) + timedelta(days=3)
        slot = AvailabilitySlot(
            professional=professional, starts_at=start, ends_at=start + timedelta(hours=1)
        )
        appointment = Appointment(
            availability_slot=slot,
            professional=professional,
            service=service,
            modality="virtual",
            contact_name="Persona de prueba",
            contact_email="persona@example.test",
            public_ticket_hash="e" * 64,
            status="pending",
            hold_expires_at=None,
        )
        db.session.add_all([service, professional, slot, appointment])
        with pytest.raises(IntegrityError):
            db.session.flush()


@pytest.mark.parametrize(
    ("provided_url", "expected_url"),
    [
        ("postgres://demo:placeholder@db.example/qaa?sslmode=require", "postgresql+psycopg://demo:placeholder@db.example/qaa?sslmode=require"),
        ("postgresql://demo:placeholder@db.example/qaa", "postgresql+psycopg://demo:placeholder@db.example/qaa"),
        ("sqlite:///qaa.sqlite3", "sqlite:///qaa.sqlite3"),
    ],
)
def test_url_postgresql_se_normaliza_a_psycopg_3(provided_url, expected_url):
    assert normalize_database_url(provided_url) == expected_url
