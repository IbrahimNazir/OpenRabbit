# ADR-0008: Docker Compose for Local Development

| Field | Value |
|-------|-------|
| **ID** | ADR-0008 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | devex, docker, infrastructure, local-dev |

---

## Context and Problem Statement

OpenRabbit depends on four external services: PostgreSQL, Redis, Qdrant, and (for local webhook testing) a smee.io relay client. Every developer who wants to contribute — and every user who wants to self-host — needs all four services running correctly with the right versions and configurations.

Without a standardized approach, contributors spend hours debugging "works on my machine" issues: wrong PostgreSQL version, Redis not started, Qdrant port conflict.

---

## Decision

**Use Docker Compose as the single, canonical way to run all infrastructure services, for both development and production self-hosting.**

The application code (FastAPI + Celery workers) runs natively on the host during development for fast iteration, while all stateful services run in Docker.

### docker-compose.yml

```yaml
version: "3.9"
name: openrabbit

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: openrabbit
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-devpassword}
      POSTGRES_DB: openrabbit
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U openrabbit"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks:
      - openrabbit_net

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: >
      redis-server
      --appendonly yes
      --appendfsync everysec
      --maxmemory 512mb
      --maxmemory-policy allkeys-lru
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10
    networks:
      - openrabbit_net

  qdrant:
    image: qdrant/qdrant:latest
    restart: unless-stopped
    ports:
      - "6333:6333"   # REST API
      - "6334:6334"   # gRPC
    volumes:
      - qdrant_data:/qdrant/storage
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/health"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks:
      - openrabbit_net

  smee:
    image: node:18-alpine
    restart: unless-stopped
    command: >
      sh -c "npm install -g smee-client &&
             smee --url ${SMEE_URL} --target http://host.docker.internal:8000/api/webhooks/github"
    extra_hosts:
      - "host.docker.internal:host-gateway"   # Linux compatibility
    profiles:
      - dev     # Only started with: docker compose --profile dev up
    networks:
      - openrabbit_net

  # Production application services (commented out for dev — run natively)
  # Uncomment for full Docker deployment
  # api:
  #   build: .
  #   ...

volumes:
  postgres_data:
  redis_data:
  qdrant_data:

networks:
  openrabbit_net:
    driver: bridge
```

### Why Compose over alternatives?

| Option | Zero-install overhead | Reproducible | Self-host friendly | Learning curve |
|--------|----------------------|--------------|-------------------|----------------|
| **Docker Compose** | ✅ (Docker Desktop) | ✅ | ✅ | Low |
| Kubernetes (local) | ❌ (minikube/kind) | ✅ | ❌ Complex | High |
| Manual installation | ❌ Per-service setup | ❌ | ⚠️ | Low per service |
| Nix/Devcontainer | ❌ Nix install | ✅ | ❌ | High |

### `--profile dev` pattern

The smee relay client only needs to run during local development (where the API is on localhost). By using `profiles: [dev]`, `docker compose up` (without profiles) starts only postgres/redis/qdrant. `docker compose --profile dev up` adds smee for local webhook testing.

---

## Consequences

### Positive
- `docker compose up -d` gets any contributor to a working state in under 2 minutes
- `postgres:16-alpine` and `redis:7-alpine` use Alpine base images — small, fast to pull
- Persistent volumes mean data survives `docker compose restart`
- Health checks ensure dependent services start only after dependencies are ready

### Negative
- Docker Desktop required on macOS/Windows (free for individual use; paid for large companies). **Mitigation:** documented in README with alternatives (Colima, Podman)
- `host.docker.internal` works differently on Linux vs macOS. **Mitigation:** `extra_hosts: ["host.docker.internal:host-gateway"]` in the smee service handles Linux
