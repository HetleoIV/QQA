# SQLite Migration Atomicity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Native implementation in this session.

**Goal:** Make SQLite Alembic upgrades and downgrades atomic, validate foreign keys before commit, and cover recovery and successful downgrade.

**Architecture:** Disable SQLite foreign-key enforcement outside the transaction, explicitly begin a real SQLite transaction for Alembic DDL, check foreign keys before commit, then restore enforcement. Roll back and invalidate a connection if cleanup cannot restore a known safe state.

**Tech Stack:** Python 3.14, sqlite3, SQLAlchemy 2.1, Alembic 1.20, pytest.

**Spec:** User authorization dated 2026-11-20, points 1–4.

## Global Constraints

- Do not modify the initial migration `934475c0b3e8`.
- Do not modify `requirements.txt`.
- Do not implement `hold_expires_at` changes or add another migration for that rule.
- Do not access Neon or Render; do not commit or push.
- Preserve all existing data.

## Review Focus

- SQLite DDL rollback: inject failure after batch table creation and verify revision/schema/data rollback.
- Foreign-key PRAGMA restoration: verify a fresh pooled connection reports `1` after failure.
- Retry after failure: run the upgrade again and verify success.
- Successful downgrade: assigned appointment and related rows survive with old constraints restored.
- Failed foreign-key check: check occurs before commit and aborts the transaction.

---

### Task 1: Make SQLite migration execution atomic

**Files:** Modify `migrations/env.py`.

- [ ] Verify driver transaction behavior with the current Python/SQLite versions.
- [ ] Start one explicit real SQLite transaction after disabling foreign keys; let Alembic use that transaction without nesting.
- [ ] Run `PRAGMA foreign_key_check` before commit; commit only after a clean result.
- [ ] Roll back on migration/configuration/check failure, restore foreign-key enforcement, and invalidate the connection if cleanup itself fails.

### Task 2: Test interrupted migration and recovery

**Files:** Modify `tests/test_migrations.py`.

- [ ] Inject a failure after SQLite batch DDL has created its temporary table.
- [ ] Assert previous Alembic revision, original schema/data, no temporary table, and clean foreign-key check.
- [ ] Assert a fresh connection has foreign keys enabled and a subsequent upgrade succeeds.

### Task 3: Test successful downgrade

**Files:** Modify `tests/test_migrations.py`.

- [ ] Upgrade a temporary database with an assigned appointment and related history/notification rows.
- [ ] Downgrade successfully and assert old nullability/index/constraints and preserved related data.

### Task 4: Verify the authorized scope

- [ ] Run `python -m pytest -q` and `git diff --check`.
- [ ] Confirm no changes to `requirements.txt` or the initial migration.
