"""Bloqueo compartido para cambios concurrentes de cuentas administrativas."""

from sqlalchemy import text


POSTGRESQL_ADMIN_MUTATIONS_LOCK_ID = 0x514141


def lock_postgresql_admin_mutations(session) -> None:
    """Serializa mutaciones de administradores dentro de la transacción actual."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": POSTGRESQL_ADMIN_MUTATIONS_LOCK_ID},
    )
