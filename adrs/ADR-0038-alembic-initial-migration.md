# ADR-0038: Alembic Migration — Verify Setup and Regenerate

## Status
Accepted

## Context
The Alembic setup already exists (`alembic.ini`, `migrations/env.py`, `migrations/versions/`). The `env.py` is properly configured with `get_settings().sync_database_url` and imports all models. Two migration files exist:
- `a1fc1f32e4c9_add_foreign_key_to_finding.py`
- `f64701a88bd6_add_fk.py`

The task requires verifying that `--autogenerate` captures all tables and generating a fresh initial schema migration.

## Decision
- Verify `env.py` imports are complete (they already are).
- Generate a new migration via `uv run alembic revision --autogenerate -m "initial_schema"`.
- Review the output to confirm all 5 tables are detected: `installations`, `repositories`, `pr_reviews`, `findings`, `conversation_threads`.
- Do NOT run `alembic upgrade head`.

## Consequences
- A verified migration file captures the complete schema.
- Future model changes can be auto-generated from this baseline.
