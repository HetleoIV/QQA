# Public Appointment Request Implementation Plan

> **For agentic workers:** Native implementation in this session.

**Goal:** Let visitors submit an unassigned appointment request through the existing public page.

**Architecture:** Extend the existing home route to render active services and process validated POST requests. Reuse the Appointment model, store only a hash of a random ticket, use a one-time session form token and post/redirect/get, and keep the existing page layout and style system.

**Tech Stack:** Flask, SQLAlchemy, Jinja, existing CSS/JavaScript, pytest.

**Spec:** User request dated 2026-11-20.

## Constraints

- Do not change the schema, either migration, `.env`, dependencies, or production services.
- Do not select or create a professional or time slot.
- Do not add email or ticket lookup behavior.
- Keep the change limited to the public appointment form and its tests.

## Tasks

### Task 1: Public route and server validation

**Files:** Modify `app/public.py`.

- [ ] Render active services and process form POSTs.
- [ ] Validate CSRF token, service, modality, name, email, phone, and privacy consent server-side.
- [ ] Persist a pending unassigned appointment in a transaction with a hashed random ticket.
- [ ] Use post/redirect/get and display a generic database failure message.

### Task 2: Existing public form

**Files:** Modify `app/templates/index.html`, `app/static/js/main.js`, `app/static/css/styles.css`.

- [ ] Replace demo service/modality/date/time controls with server-provided services, supported modalities, required contact fields, and explicit privacy consent.
- [ ] Show field errors and a one-time ticket confirmation; remove the fake client-only success message.
- [ ] Preserve current responsive layout and prevent accidental repeat clicks.

### Task 3: Focused coverage

**Files:** Modify `tests/test_app.py`.

- [ ] Cover valid creation, required/invalid fields, inactive/missing service, consent, unique tickets, initial null assignment/expiry, replay prevention, and safe database errors.

### Task 4: Verify

- [ ] Run focused public-form tests, then `./.venv/bin/python -m pytest -q`.
- [ ] Run `git diff --check` and confirm no schema or dependency changes.
