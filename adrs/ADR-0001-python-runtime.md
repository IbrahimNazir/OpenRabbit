# ADR-0001: Python 3.12 as Primary Runtime

| Field | Value |
|-------|-------|
| **ID** | ADR-0001 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | runtime, language, tooling |

---

## Context and Problem Statement

OpenRabbit is an AI-native application whose core responsibilities are: handling asynchronous webhook events, integrating with AI APIs (Anthropic Claude), performing code parsing (Tree-sitter), managing vector databases (Qdrant), and orchestrating complex multi-stage LLM pipelines.

We must choose a primary programming language and runtime version that satisfies all of these requirements efficiently and with the broadest available library ecosystem.

The language choice governs:
- Library availability (AI SDKs, AST parsers, vector DB clients)
- Async capability (critical for high-throughput webhook handling)
- Typing support (critical for a maintainable open-source codebase)
- Developer familiarity (open-source contributors)
- Tooling maturity (linters, formatters, test runners)

---

## Decision Drivers

1. **AI ecosystem breadth** — All major AI providers (Anthropic, OpenAI) publish official Python SDKs first
2. **Async-native capability** — Webhook handling must return HTTP 200 in <100ms; processing must be asynchronous
3. **Code parsing libraries** — Tree-sitter has a first-class Python binding (`tree-sitter` 0.20+)
4. **Vector DB support** — Qdrant's Python SDK is the most feature-complete of their language clients
5. **Open-source contributor base** — Python has the largest pool of backend + ML contributors
6. **Type safety** — We need mypy + typing for a maintainable, long-lived open-source project
7. **Runtime performance** — Python 3.12 includes significant performance improvements (up to 15% faster than 3.11 per CPython benchmarks)

---

## Considered Options

| Option | AI SDKs | Async | Tree-sitter | Type System | Contributor Base |
|--------|---------|-------|-------------|-------------|-----------------|
| **Python 3.12** | ✅ Excellent | ✅ asyncio native | ✅ First-class | ✅ mypy + PEP 695 | ✅ Largest |
| Python 3.11 | ✅ Excellent | ✅ asyncio native | ✅ First-class | ✅ mypy | ✅ Largest |
| TypeScript/Node 20 | ✅ Good | ✅ Promises | ⚠️ Partial bindings | ✅ TypeScript | ✅ Large |
| Go 1.22 | ⚠️ Community | ✅ goroutines | ⚠️ CGO bindings | ✅ Native | ⚠️ Medium |
| Rust | ❌ Minimal | ✅ tokio | ⚠️ Via bindings | ✅ Native | ❌ Small (AI) |

---

## Decision

**Use Python 3.12 specifically** (not 3.11 or 3.10).

### Rationale

Python 3.12 introduces two improvements directly relevant to this project:

1. **Per-interpreter GIL (PEP 684)** — While we use Celery workers (not threads) for parallelism, the interpreter improvements benefit asyncio event loop performance.

2. **Better error messages and f-string syntax (PEP 701)** — Directly improves debugging speed during the 20-day sprint.

3. **Type system improvements (PEP 695)** — `type` statement for type aliases makes the codebase cleaner for contributors.

4. **15% performance improvement over 3.11** — Meaningful for a webhook handler processing hundreds of events per minute.

Node/TypeScript was seriously considered because of its strong async model and TypeScript's excellent typing. It was rejected because:
- The Anthropic Python SDK is more feature-complete than the TypeScript SDK (streaming, structured output, tool use)
- Tree-sitter's Python bindings are first-class; the Node bindings require more workarounds
- The ML/AI open-source community contribution base is overwhelmingly Python

---

## Consequences

### Positive
- All Anthropic, OpenAI, and Qdrant SDKs available as first-class packages
- Tree-sitter, NetworkX, tiktoken all have native Python packages with no FFI overhead
- FastAPI, Celery, SQLAlchemy — all Python-native, battle-tested, well-documented
- Largest pool of potential open-source contributors

### Negative
- Python has the GIL — CPU-bound tasks (Tree-sitter parsing of large files) block the event loop. **Mitigation:** all CPU-bound work runs in Celery workers (separate processes), not in the FastAPI async event loop
- Python startup time is slower than Go/Node for cold starts. **Mitigation:** workers are long-running processes, not serverless functions
- Memory usage is higher than Go/Rust. **Mitigation:** acceptable for a self-hosted Docker Compose deployment

### Neutral
- Requires Python 3.12+ in deployment — documented in README and enforced in `pyproject.toml` with `python = "^3.12"`

---

## Implementation Notes

```toml
# pyproject.toml
[tool.poetry.dependencies]
python = "^3.12"
```

```dockerfile
# Dockerfile
FROM python:3.12-slim
```

```yaml
# .github/workflows/ci.yml
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"
```

Enforce at runtime:
```python
# app/main.py
import sys
assert sys.version_info >= (3, 12), "OpenRabbit requires Python 3.12+"
```

---

## Review Date

Revisit when Python 3.13 reaches stable (expected October 2025) — evaluate free-threaded mode (no-GIL) for CPU-bound parsing tasks.
