"""Comandos seguros para cargar los datos demostrativos en SQLite local."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import click
from flask.cli import with_appcontext
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import generate_password_hash

from .admin_account_lock import (
    POSTGRESQL_ADMIN_MUTATIONS_LOCK_ID,
    lock_postgresql_admin_mutations,
)
from .extensions import db
from .models import Administrator, AvailabilitySlot, Professional, Service


EXPECTED_SERVICE_SLUGS = {
    "psicologia-clinica",
    "psicologia-forense",
    "psicologia-organizacional",
    "asesoria-legal",
    "orientacion-nutricional",
}

DEMO_PROFESSIONALS = (
    ("Apolaya Paucar, Jaime Martín", "psicologia-clinica"),
    ("Huarcaya Sifuentes, Kevin Albert", "psicologia-forense"),
    ("Quelca Apari, Cesar Anderson", "psicologia-organizacional"),
)

DEMO_DATES = (date(2027, 1, 4), date(2027, 1, 5), date(2027, 1, 6))
DEMO_START_TIMES = (time(9, 0), time(10, 0))
DEMO_TIMEZONE = ZoneInfo("America/Lima")
POSTGRESQL_ADMIN_CREATION_LOCK_ID = POSTGRESQL_ADMIN_MUTATIONS_LOCK_ID


def _require_sqlite() -> None:
    """Evita que los comandos de datos iniciales se ejecuten en PostgreSQL."""
    if db.engine.dialect.name != "sqlite":
        raise click.ClickException(
            "Este comando está habilitado únicamente para SQLite local y pruebas."
        )


def _professional_key(full_name: str) -> str:
    return " ".join(full_name.split()).casefold()


def _prompt_administrator_credentials() -> tuple[str, str]:
    """Solicita y valida correo y contraseña sin mostrar la contraseña."""
    email = click.prompt("Correo electrónico del administrador").strip().lower()
    if (
        len(email) > 254
        or email.count("@") != 1
        or any(character.isspace() for character in email)
    ):
        raise click.BadParameter("Ingresa un correo electrónico válido.", param_hint="correo")

    password = click.prompt(
        "Contraseña (mínimo 12 caracteres)", hide_input=True, confirmation_prompt=True
    )
    if len(password) < 12:
        raise click.BadParameter(
            "La contraseña debe tener al menos 12 caracteres.", param_hint="contraseña"
        )
    return email, password


def _require_postgresql() -> None:
    """Evita que el comando de producción se ejecute contra otro motor."""
    if db.engine.dialect.name != "postgresql":
        raise click.ClickException(
            "Este comando está habilitado únicamente para PostgreSQL."
        )


def _lock_postgresql_admin_creation() -> None:
    """Serializa las ejecuciones del comando dentro de una transacción."""
    lock_postgresql_admin_mutations(db.session)


@click.command("seed-initial-data")
@with_appcontext
def seed_initial_data() -> None:
    """Añade los perfiles y horarios aprobados sin actualizar datos existentes."""
    _require_sqlite()
    created_professionals = 0
    created_service_links = 0
    created_slots = 0
    skipped_slots = 0
    required_slugs = EXPECTED_SERVICE_SLUGS

    try:
        with db.session.begin():
            services = {
                service.slug: service
                for service in db.session.scalars(
                    select(Service).where(Service.slug.in_(required_slugs))
                ).all()
            }
            missing_services = required_slugs - services.keys()
            if missing_services:
                raise click.ClickException(
                    "Faltan servicios de la migración inicial: "
                    + ", ".join(sorted(missing_services))
                )

            existing_professionals = db.session.scalars(select(Professional)).all()
            professionals_by_name: dict[str, list[Professional]] = {}
            for professional in existing_professionals:
                key = _professional_key(professional.full_name)
                professionals_by_name.setdefault(key, []).append(professional)

            selected_professionals: list[tuple[Professional, Service]] = []
            for full_name, service_slug in DEMO_PROFESSIONALS:
                key = _professional_key(full_name)
                matches = professionals_by_name.get(key, [])
                if len(matches) > 1:
                    raise click.ClickException(
                        f"Hay más de un perfil con el nombre {full_name}; "
                        "se requiere revisión manual antes de cargar datos."
                    )

                if matches:
                    professional = matches[0]
                else:
                    professional = Professional(
                        full_name=full_name,
                        description="Psicólogo - Perfil",
                    )
                    db.session.add(professional)
                    professionals_by_name[key] = [professional]
                    created_professionals += 1

                service = services[service_slug]
                if service not in professional.services:
                    professional.services.append(service)
                    created_service_links += 1
                selected_professionals.append((professional, service))

            # Obtiene los identificadores antes de comprobar/crear horarios.
            db.session.flush()

            for professional, _service in selected_professionals:
                for demo_date in DEMO_DATES:
                    for local_time in DEMO_START_TIMES:
                        local_start = datetime.combine(
                            demo_date, local_time, tzinfo=DEMO_TIMEZONE
                        )
                        starts_at = local_start.astimezone(timezone.utc)
                        ends_at = starts_at + timedelta(hours=1)

                        exact_slot = db.session.scalar(
                            select(AvailabilitySlot.id).where(
                                AvailabilitySlot.professional_id == professional.id,
                                AvailabilitySlot.starts_at == starts_at,
                                AvailabilitySlot.ends_at == ends_at,
                            )
                        )
                        if exact_slot is not None:
                            skipped_slots += 1
                            continue

                        overlapping_slot = db.session.scalar(
                            select(AvailabilitySlot.id).where(
                                AvailabilitySlot.professional_id == professional.id,
                                AvailabilitySlot.starts_at < ends_at,
                                AvailabilitySlot.ends_at > starts_at,
                            )
                        )
                        if overlapping_slot is not None:
                            # Conserva el horario existente y evita crear un cruce.
                            skipped_slots += 1
                            continue

                        db.session.add(
                            AvailabilitySlot(
                                professional_id=professional.id,
                                starts_at=starts_at,
                                ends_at=ends_at,
                                is_enabled=True,
                            )
                        )
                        created_slots += 1
    except (IntegrityError, ValueError) as error:
        db.session.rollback()
        raise click.ClickException(
            "La carga se revirtió porque una restricción detectó datos incompatibles."
        ) from error

    click.echo(
        "Carga inicial completada. "
        f"Perfiles nuevos: {created_professionals}; "
        f"relaciones nuevas: {created_service_links}; "
        f"horarios nuevos: {created_slots}; "
        f"horarios existentes o en conflicto omitidos: {skipped_slots}."
    )


@click.command("create-initial-admin")
@with_appcontext
def create_initial_admin() -> None:
    """Crea el primer administrador con contraseña oculta y almacenada como hash."""
    _require_sqlite()

    existing_admin_id = db.session.scalar(select(Administrator.id).limit(1))
    db.session.rollback()
    if existing_admin_id is not None:
        click.echo("Ya existe una cuenta administrativa; no se realizaron cambios.")
        return

    email, password = _prompt_administrator_credentials()

    try:
        # BEGIN IMMEDIATE serializa dos intentos simultáneos en SQLite.
        db.session.execute(text("BEGIN IMMEDIATE"))
        existing_admin_id = db.session.scalar(select(Administrator.id).limit(1))
        if existing_admin_id is not None:
            db.session.rollback()
            click.echo("Ya existe una cuenta administrativa; no se realizaron cambios.")
            return

        db.session.add(
            Administrator(
                email=email,
                password_hash=generate_password_hash(password),
            )
        )
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise click.ClickException(
            "No se pudo crear la cuenta; revisa si ya existe una cuenta administrativa."
        ) from error

    click.echo("Cuenta administrativa inicial creada correctamente.")


@click.command("create-postgresql-admin")
@with_appcontext
def create_postgresql_admin() -> None:
    """Crea el primer administrador en PostgreSQL sin reemplazar existentes."""
    _require_postgresql()

    try:
        existing_admin_id = db.session.scalar(select(Administrator.id).limit(1))
        db.session.rollback()
    except SQLAlchemyError:
        db.session.rollback()
        raise click.ClickException(
            "No se pudo consultar el estado de las cuentas administrativas."
        ) from None
    if existing_admin_id is not None:
        click.echo("Ya existe una cuenta administrativa; no se realizaron cambios.")
        return

    email, password = _prompt_administrator_credentials()
    password_hash = generate_password_hash(password)
    existing_admin = False

    try:
        with db.session.begin():
            # Todas las ejecuciones de este comando comparten el mismo bloqueo.
            _lock_postgresql_admin_creation()
            existing_admin = (
                db.session.scalar(select(Administrator.id).limit(1)) is not None
            )
            if not existing_admin:
                db.session.add(
                    Administrator(email=email, password_hash=password_hash)
                )
                db.session.flush()
    except SQLAlchemyError:
        db.session.rollback()
        raise click.ClickException(
            "No se pudo crear la cuenta administrativa; la operación se revirtió."
        ) from None

    if existing_admin:
        click.echo("Ya existe una cuenta administrativa; no se realizaron cambios.")
        return
    click.echo("Cuenta administrativa inicial creada correctamente.")


def register_seed_commands(app) -> None:
    """Registra los comandos seguros de inicialización en Flask CLI."""
    app.cli.add_command(seed_initial_data)
    app.cli.add_command(create_initial_admin)
    app.cli.add_command(create_postgresql_admin)
