# ADR-0009: Poetry for Dependency Management

| Field | Value |
|-------|-------|
| **ID** | ADR-0009 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | tooling, dependencies, python, packaging |

---

## Context and Problem Statement

OpenRabbit has ~25 production dependencies and ~10 development dependencies. Dependency management for an open-source Python project must: lock exact versions for reproducibility, separate runtime from dev dependencies, and produce a standard-format lock file that contributors can trust.

---

## Decision

**Use Poetry 1.8+ for dependency management and packaging.**

```toml
# pyproject.toml
[tool.poetry]
name = "openrabbit"
version = "0.1.0"
description = "AI-powered GitHub PR reviewer"
license = "MIT"
python = "^3.12"

[tool.poetry.dependencies]
fastapi = "^0.115"
uvicorn = {extras = ["standard"], version = "^0.30"}
celery = {extras = ["redis"], version = "^5.4"}
redis = "^5.0"
sqlalchemy = {extras = ["asyncio"], version = "^2.0"}
asyncpg = "^0.29"
alembic = "^1.13"
anthropic = "^0.40"
PyGithub = "^2.4"
httpx = "^0.27"
pydantic-settings = "^2.5"
qdrant-client = "^1.12"
tree-sitter = "^0.23"
networkx = "^3.4"
tiktoken = "^0.8"
structlog = "^24.4"
python-jose = {extras = ["cryptography"], version = "^3.3"}

[tool.poetry.dev-dependencies]
pytest = "^8.3"
pytest-asyncio = "^0.24"
pytest-mock = "^3.14"
httpx = "^0.27"   # for TestClient
ruff = "^0.7"
mypy = "^1.11"
black = "^24.10"

[tool.ruff]
line-length = 100
select = ["E", "F", "I", "N", "W", "UP"]  # Enable: pyflakes, isort, pep8-naming, pyupgrade

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true
```

**Why Poetry over pip + requirements.txt?**
- `poetry.lock` provides deterministic installs — exact same versions on every machine, every CI run
- `poetry add` resolves transitive dependencies automatically and checks for conflicts
- Separates `[tool.poetry.dependencies]` from `[tool.poetry.dev-dependencies]` cleanly
- Standard `pyproject.toml` format (PEP 517/518) means the project is pip-installable too
- `poetry build` produces distributable wheel — important for future PyPI publication

**Why not pip-tools?** pip-tools (pip-compile) is excellent but requires maintaining `requirements.in` separately from `pyproject.toml`. Poetry unifies both in one file.
