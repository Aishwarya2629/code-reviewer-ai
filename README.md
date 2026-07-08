<div align="center">

# 🔍 AI Code Reviewer

**A distributed multi-agent AI system for code review, security scanning, complexity analysis, and DSA problem solving.**

[![CI](https://github.com/Aishwarya2629/code-reviewer-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/aishwarya2629/code-reviewer-ai/actions)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2-6B46C1)](https://langchain-ai.github.io/langgraph)
[![Celery](https://img.shields.io/badge/Celery-5.4-37814A?logo=celery)](https://docs.celeryq.dev)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)](https://redis.io)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL+pgvector-16-336791?logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose)
[![License: MIT](https://img.shields.io/badge/License-MIT-F59E0B)](LICENSE)

[**Demo**](demo.gif) · [**API Docs**](https://fastapi.tiangolo.com/) · [**Architecture**](project/docs/ARCHITECTURE.md) · [**Deployment Guide**](DEPLOYMENT.md)

</div>

---

## What This Is

Most "AI code reviewers" are a single LLM prompt wrapped in a web form. This system treats code review as what it actually is — a multi-step engineering process. Six specialised agent nodes handle each concern independently: language detection, security analysis, complexity measurement, optimisation, validation, and explanation. Failed optimisations roll back automatically. Repeated reviews return from a semantic cache in milliseconds. The system stays responsive under load because Celery decouples submission from processing. Every LLM provider failure is handled by a circuit-breaker-protected fallback chain.

---

## How It Differs From a Typical Project

| Feature | Typical "AI Code Reviewer" | This Project |
|---|---|---|
| LLM calls per review | 1 monolithic prompt | 6 specialised agent nodes |
| Security scanning | ❌ None | ✅ 10 regex SAST rules + LLM contextual scan |
| Async processing | ❌ Blocks for 10–30 s | ✅ Returns `job_id` in < 100 ms; Celery processes async |
| Duplicate reviews | ❌ Full LLM cost every time | ✅ Two-tier cache: SHA-256 exact match + pgvector cosine similarity |
| LLM provider failure | ❌ App goes down | ✅ Circuit breaker + 5-provider fallback chain |
| Multi-tenancy | ❌ Single user | ✅ API-key auth, per-tenant rate limits, scoped analytics |
| GitHub integration | ❌ None | ✅ Webhook auto-reviews PRs, posts comment |
| Observability | ❌ `print()` | ✅ Prometheus metrics, JSON structured logs, analytics dashboard |
| Broken optimisation handling | ❌ Silently corrupts code | ✅ Validator node rolls back to original |
| Tests | ❌ None | ✅ 40+ pytest cases, all run without API keys |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Streamlit Frontend  :8501                     │
│         Code Review │ Problem Solver │ Image OCR │ Analytics    │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP  (X-Request-ID header)
┌───────────────────────────▼─────────────────────────────────────┐
│                  FastAPI Backend  :8000                          │
│                                                                  │
│  TenantMiddleware (API key → tier → RPM)                        │
│  RateLimiter (Redis sliding window per tenant)                  │
│  Request-ID Middleware (UUID → all log lines + response header) │
│  Global Exception Handler (domain errors → correct HTTP codes)  │
│  Prometheus Middleware (all requests instrumented)              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────┐     │
│  │               /api/v1  Router                          │     │
│  │  /review   /problem   /image   /jobs   /webhooks       │     │
│  │  /analytics/*   /health   /metrics                     │     │
│  └────────────────────┬───────────────────────────────────┘     │
└───────────────────────┼─────────────────────────────────────────┘
                        │
        ┌───────────────┴──────────────────┐
        │ Sync                             │ Async (?async=true)
        ▼                                  ▼
┌───────────────┐                 ┌──────────────────────┐
│ Semantic Cache│                 │   Celery Queue        │
│ lookup first  │                 │   broker: Redis :6379 │
└──────┬────────┘                 │   result: Redis :6379 │
       │ miss                     └──────────┬───────────┘
       ▼                                     │
┌──────────────────────────────────────────────────────────────┐
│                  LangGraph Agent Pipeline                     │
│                                                              │
│  ┌────────────┐                                              │
│  │ 1.Classify │ detect language · input type (code/problem) │
│  └──────┬─────┘  invalid → end_invalid (saves 5 LLM calls) │
│         │ valid                                              │
│  ┌──────▼──────┐                                            │
│  │ 2.Security  │ Layer 1: 10 regex rules @ ~5 ms, $0 cost  │
│  │   Scanner   │ Layer 2: LLM contextual scan (subtle bugs) │
│  └──────┬──────┘                                            │
│         │                                                    │
│  ┌──────▼──────┐                                            │
│  │ 3.Complexity│ LLM analysis of original code              │
│  │  Analyzer   │ Establishes BEFORE baseline for comparison │
│  └──────┬──────┘                                            │
│         │                                                    │
│  ┌──────▼──────┐                                            │
│  │ 4.Optimizer │ Full context: language + complexity +       │
│  │             │ security findings → informed optimisation  │
│  └──────┬──────┘                                            │
│         │                    ┌────────────────────────┐     │
│  ┌──────▼──────┐             │   If validation fails: │     │
│  │ 5.Validator │─── fail ───▶│   rollback to original │     │
│  └──────┬──────┘             │   code automatically   │     │
│         │ pass               └────────────────────────┘     │
│  ┌──────▼──────┐                                            │
│  │ 6.Explainer │ after complexity · narrative · analysis    │
│  └─────────────┘                                            │
└──────────────────────────────────────────────────────────────┘
                        │
        ┌───────────────┼────────────────────────┐
        ▼               ▼                        ▼
┌──────────────┐ ┌──────────────┐      ┌──────────────────┐
│  LLM Service │ │ Semantic     │      │  PostgreSQL :5432 │
│  5-provider  │ │ Cache        │      │  + pgvector       │
│  fallback    │ │ (pgvector)   │      │                   │
│              │ │              │      │  Tables:          │
│ Gemini Pro   │ │ Tier 1:      │      │  tenants          │
│ Gemini Flash │ │ SHA-256 hash │      │  review_cache     │
│ Groq LLaMA3  │ │ (< 1 ms)    │      │  request_metrics  │
│ OpenRouter   │ │              │      │                   │
│ DeepSeek     │ │ Tier 2:      │      │  Indexes:         │
│ Mistral      │ │ cosine sim   │      │  hash_idx         │
│              │ │ ≥ 0.93       │      │  ivfflat on       │
│ Circuit      │ │ (~5–20 ms)   │      │  embedding col    │
│ breaker per  │ └──────────────┘      └──────────────────┘
│ provider     │
│ (Redis)      │
└──────────────┘
```

---

## Agent Pipeline — Node Reference

| # | Node | Responsibility | Fallback if LLM fails |
|---|---|---|---|
| 1 | **Classifier** | Detects language + input type. Invalid input → early exit (saves 5 LLM calls) | Regex heuristics (`def `, `public class`, etc.) |
| 2 | **Security Scanner** | Layer 1: 10 regex SAST rules (~5 ms, $0). Layer 2: LLM contextual scan | Regex findings still surface |
| 3 | **Complexity Analyzer** | Establishes BEFORE baseline (time + space) for comparison | Python AST loop-nesting heuristic |
| 4 | **Optimizer** | Receives full context: language + complexity + security. Only claims improvement where measurable | Returns original code unchanged |
| 5 | **Validator** | Second LLM verifies optimisation correctness. Rolls back to original if broken | Skips validation, keeps optimisation |
| 6 | **Explainer** | Computes AFTER complexity. Generates technical analysis + developer narrative | Static template explanation |

---

## Security Scanner — Rule Reference

Two-layer scanning runs on every submission before optimisation:

| Rule ID | Severity | What It Detects |
|---|---|---|
| SEC-001 | 🔴 CRITICAL | Hardcoded API keys / passwords / tokens in source |
| SEC-002 | 🟠 HIGH | `subprocess(shell=True)` — shell injection vector |
| SEC-003 | 🟠 HIGH | `os.system()` — direct shell execution |
| SEC-004 | 🟠 HIGH | `eval()` called with user-controlled input |
| SEC-005 | 🟠 HIGH | `pickle.loads()` — arbitrary object deserialisation |
| SEC-006 | 🟡 MEDIUM | `yaml.load()` without an explicit safe Loader |
| SEC-007 | 🟡 MEDIUM | MD5 / SHA-1 used for password hashing |
| SEC-008 | 🟠 HIGH | SQL queries built with f-strings — injection risk |
| SEC-009 | 🟡 MEDIUM | File paths derived from user input — path traversal |
| SEC-010 | 🟢 LOW | Credentials or secrets printed to stdout |
| LLM-* | varies | Context-dependent issues beyond regex capability |

---

## LLM Provider Fallback Chain

```
Request
   │
   ├─▶ Gemini 2.5 Pro      (primary — highest quality)
   │       │ fail
   ├─▶ Gemini 2.5 Flash    (faster, cheaper)
   │       │ fail
   ├─▶ Groq LLaMA3 70B     (very fast, generous free tier)
   │       │ fail
   ├─▶ OpenRouter           (aggregator — many models)
   │       │ fail
   ├─▶ DeepSeek Coder       (strong code model, low cost)
   │       │ fail
   ├─▶ Mistral Codestral    (code-specialised)
   │       │ fail
   └─▶ Mock response        (MOCK_MODE=true — always available)
```

Each provider is protected by a **circuit breaker** stored in Redis:

```
CLOSED ──(5 failures in 60 s)──▶ OPEN ──(30 s)──▶ HALF_OPEN
  ▲                                                     │
  └──────────────(1 success)───────────────────────────┘
                                (1 failure) → OPEN again
```

The breaker **fails open** (allows all calls) if Redis is unavailable — the observability layer never becomes a single point of failure.

---

## Semantic Cache

Every review result is cached in PostgreSQL + pgvector with two lookup tiers:

```
Incoming code
      │
      ▼
Tier 1: SHA-256 exact match
      │ hit → return in < 1 ms, $0 LLM cost
      │ miss
      ▼
Tier 2: Embed code → pgvector cosine similarity search (ivfflat index)
      │ similarity ≥ 0.93 → return in ~5–20 ms, $0 LLM cost
      │ miss
      ▼
Run full 6-node pipeline → store result in cache
```

Tier 2 catches: same algorithm with renamed variables, reformatted code, added comments, different whitespace. The 0.93 threshold is conservative — a false cache hit is worse than a miss.

---

## Multi-Tenancy and Rate Limiting

Every request is authenticated via `X-API-Key` header:

```
Request header: X-API-Key: <key>
       │
       ▼
TenantMiddleware → lookup in Redis cache (5-min TTL)
                 → fallback: PostgreSQL tenants table
                 → fallback: DEFAULT_TENANT_API_KEY (dev mode)
       │
       ▼
Resolve tier → RPM limit → Redis sliding window check
```

| Tier | Rate Limit | Features |
|---|---|---|
| Free | 10 req/min | Review, problem solver, image OCR |
| Pro | 60 req/min | + GitHub webhook, async queue, analytics |
| Enterprise | 600 req/min | + Custom model config, dedicated queues |

Rate limiting **fails open** without Redis — the limiter never blocks all traffic during an outage.

---

## GitHub Webhook — How It Works

1. Developer opens or updates a PR
2. GitHub sends `POST /api/v1/webhooks/github` with HMAC-SHA256 signature
3. Endpoint verifies signature against `GITHUB_WEBHOOK_SECRET` → 403 if invalid
4. Fetches changed files via GitHub API (parallel HTTP GETs, ~1–2 s)
5. Enqueues **one coordinator Celery task** and returns `202 Accepted` in < 2 s
6. GitHub marks delivery as successful (well within its 10-second timeout)
7. Coordinator task runs the 6-node pipeline on each changed file, then posts a single consolidated PR review comment

The coordinator pattern ensures: no duplicate comments from GitHub webhook retries, tasks are retryable as a unit, and no HTTP timeout constraint during processing.

---

## Observability

### Prometheus Metrics (`GET /metrics`)

| Metric | Type | Labels |
|---|---|---|
| `http_requests_total` | Counter | method, endpoint, status |
| `http_request_duration_seconds` | Histogram | method, endpoint |
| `llm_requests_total` | Counter | provider, success |
| `llm_latency_seconds` | Histogram | provider |
| `pipeline_duration_seconds` | Histogram | cached (true/false) |
| `pipeline_runs_total` | Counter | language, already_optimal |
| `security_issues_found_total` | Counter | severity |
| `cache_hits_total` | Counter | — |
| `cache_misses_total` | Counter | — |
| `circuit_breaker_state` | Gauge | provider, state |
| `celery_jobs_enqueued_total` | Counter | task_name |
| `celery_active_jobs` | Gauge | — |
| `tenant_requests_total` | Counter | tenant, tier |
| `rate_limit_hits_total` | Counter | tenant |

### Analytics Dashboard (`/api/v1/analytics/*`)

| Endpoint | Returns |
|---|---|
| `/overview` | Total requests, cache hit rate, avg/p95 latency, error rate, security-flagged count |
| `/requests-over-time` | Hourly request volume + cache hit counts (time series) |
| `/providers` | Per-provider usage, avg latency, circuit breaker states |
| `/security` | Total findings by severity, affected review percentage |
| `/latency` | p50 / p95 / p99 per endpoint |
| `/languages` | Language distribution of reviewed code |

### Structured JSON Logging

Every log line carries:
```json
{
  "ts": "2026-07-04T10:30:00.123Z",
  "level": "INFO",
  "request_id": "3f2a1b4c-...",
  "module": "app.agents.nodes.optimizer",
  "msg": "Optimizer: already_optimal=False changes=3"
}
```

`request_id` is carried through all log lines via `ContextVar`, set by middleware, and returned in the `X-Request-ID` response header.

---

## Quick Start

### Prerequisites

```bash
# macOS
brew install tesseract

# Ubuntu / WSL
sudo apt install tesseract-ocr libgl1

# Windows — Tesseract installer:
# https://github.com/UB-Mannheim/tesseract/wiki
```

### Option A — Docker (Recommended)

Starts all 6 services: PostgreSQL, Redis, backend, Celery worker, Flower, frontend.

```bash
git clone https://github.com/YOUR_USERNAME/ai-code-reviewer.git
cd ai-code-reviewer

# 1. Configure environment
cp .env.example .env
# Edit .env — add at least one LLM API key (see table below)

# 2. Start everything
docker compose up --build
```

**Expected startup output:**
```
postgres  | database system is ready to accept connections
redis     | Ready to accept connections
backend   | INFO  LLM providers ready: ['gemini-primary', 'gemini-flash']
backend   | INFO  AI Code Reviewer v2.0.0 starting up
worker    | [celery] ready. Queues: reviews, problems, webhooks
flower    | INFO  Visit me at http://localhost:5555
frontend  | You can now view your Streamlit app in your browser
```

**Access points:**
| Service | URL | Description |
|---|---|---|
| Streamlit UI | http://localhost:8501 | Code review, problem solver, image upload, analytics |
| API Docs | http://localhost:8000/docs | Interactive Swagger UI |
| ReDoc | http://localhost:8000/redoc | API reference |
| Flower | http://localhost:5555 | Celery task monitor |
| Prometheus metrics | http://localhost:8000/metrics | Scrape endpoint |

### Option B — Local Development (No Docker)

```bash
# --- Terminal 1: Backend ---
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp ../.env.example ../.env         # then add your API key(s)
uvicorn app.main:app --reload --port 8000

# --- Terminal 2: Celery Worker ---
cd backend
source .venv/bin/activate
celery -A app.workers.celery_app worker \
  --loglevel=info \
  -Q reviews,problems,webhooks

# --- Terminal 3: Frontend ---
cd frontend
pip install -r requirements.txt
BACKEND_URL=http://127.0.0.1:8000 streamlit run app.py
```

> **Note:** Redis and PostgreSQL must be running locally for async jobs, caching, and analytics. Use `docker compose up postgres redis` to start just those services.

### Option C — Streamlit Community Cloud (Free, No Server)

Uses `streamlit_app.py` at the repo root — runs the **entire pipeline inside Streamlit** with no FastAPI required.

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
3. Set **Main file path:** `streamlit_app.py`
4. Under **Advanced settings → Secrets**, add:
   ```toml
   GOOGLE_API_KEY = "your_google_api_key_here"
   ```
5. Deploy — `packages.txt` installs Tesseract automatically

---

## API Reference

### POST /api/v1/review

Synchronous code review. Runs the full 6-node pipeline. For async operation, add `?async=true`.

**Request:**
```json
{
  "code": "def find_dups(arr):\n    dupes = []\n    for i in range(len(arr)):\n        for j in range(i+1, len(arr)):\n            if arr[i] == arr[j]:\n                dupes.append(arr[i])\n    return dupes",
  "language": "Python"
}
```

**Response:**
```json
{
  "request_id": "3f2a1b4c-d5e6-7f8a-9b0c-d1e2f3a4b5c6",
  "valid": true,
  "already_optimal": false,
  "detected_language": "Python",
  "original_code": "...",
  "optimized_code": "def find_dups(arr):\n    seen = set()\n    dupes = set()\n    for x in arr:\n        if x in seen:\n            dupes.add(x)\n        seen.add(x)\n    return list(dupes)",
  "before_complexity": {
    "time": "O(n²)",
    "space": "O(n)",
    "reasoning": "Nested loops over the array with an inner membership check."
  },
  "after_complexity": {
    "time": "O(n)",
    "space": "O(n)",
    "reasoning": "Single pass using a hash set for O(1) lookups."
  },
  "security_issues": [],
  "changes_made": [
    {
      "category": "algorithmic",
      "description": "Replace nested loops with hash set",
      "impact": "O(n²) → O(n)"
    }
  ],
  "explanation": "...",
  "analysis": "...",
  "provider_used": "gemini-primary",
  "fallback_used": false,
  "pipeline_ms": 8432
}
```

**Supported languages:** `auto`, `Python`, `Java`, `JavaScript`, `TypeScript`, `C++`, `Go`, `Rust`

### POST /api/v1/review?async=true

Returns immediately with a job ID. Poll for result.

```json
{ "job_id": "celery-task-uuid", "poll_url": "/api/v1/jobs/celery-task-uuid" }
```

### GET /api/v1/jobs/{job_id}

```json
{
  "job_id": "celery-task-uuid",
  "status": "SUCCESS",
  "result": { ... }
}
```

`status` values: `PENDING`, `STARTED`, `SUCCESS`, `FAILURE`

### POST /api/v1/problem

Generate 4 progressive solutions (Brute Force → Better → Optimised → Advanced).

```json
{
  "problem": "Given an array of integers and a target sum, return indices of two numbers that add up to the target.",
  "language": "Python"
}
```

### POST /api/v1/image

Upload a code screenshot or problem statement image. OCR extracts text, auto-routes to review or problem solver.

```
Content-Type: multipart/form-data
Fields: file (image/jpeg, image/png, image/webp), language (optional)
```

### POST /api/v1/webhooks/github

GitHub pull request webhook receiver. Verifies HMAC-SHA256 signature, enqueues coordinator task, returns 202 in < 2 seconds.

### Complete Endpoint Table

| Method | Endpoint | Description | Response |
|---|---|---|---|
| `GET` | `/api/v1/health` | Service health + provider availability | `200` |
| `POST` | `/api/v1/review` | Synchronous 6-node code review | `200 ReviewResponse` |
| `POST` | `/api/v1/review?async=true` | Enqueue review, return job ID | `202` |
| `POST` | `/api/v1/jobs/review` | Explicit async review submit | `202` |
| `POST` | `/api/v1/jobs/problem` | Explicit async problem submit | `202` |
| `GET` | `/api/v1/jobs/{id}` | Poll job status + result | `200` |
| `POST` | `/api/v1/problem` | DSA solver — 4 approaches | `200 ProblemResponse` |
| `POST` | `/api/v1/image` | OCR → auto-route to review/problem | `200` |
| `POST` | `/api/v1/webhooks/github` | GitHub PR webhook | `202` |
| `GET` | `/api/v1/analytics/overview` | KPI metrics | `200` |
| `GET` | `/api/v1/analytics/requests-over-time` | Hourly request volume | `200` |
| `GET` | `/api/v1/analytics/providers` | Provider usage + CB states | `200` |
| `GET` | `/api/v1/analytics/security` | Security findings summary | `200` |
| `GET` | `/api/v1/analytics/latency` | p50/p95/p99 per endpoint | `200` |
| `GET` | `/api/v1/analytics/languages` | Language distribution | `200` |
| `GET` | `/metrics` | Prometheus scrape | `200 text/plain` |
| `GET` | `/docs` | Swagger UI | — |

### HTTP Status Codes

| Code | Meaning |
|---|---|
| `200` | Success |
| `202` | Accepted — async job queued |
| `400` | Unsupported language |
| `403` | Invalid webhook signature |
| `413` | Input exceeds size limit |
| `422` | Invalid input (empty, unparseable) |
| `429` | Rate limit exceeded |
| `503` | All LLM providers unavailable / queue down |

---

## GitHub Webhook Setup

1. Go to your GitHub repo → **Settings → Webhooks → Add webhook**
2. Set **Payload URL:** `https://YOUR_DOMAIN/api/v1/webhooks/github`
3. Set **Content type:** `application/json`
4. Set **Secret:** any random string (e.g. `openssl rand -hex 32`)
5. Add that same secret as `GITHUB_WEBHOOK_SECRET` in your `.env`
6. Under **Events**, select: **Pull requests**
7. Save

Every PR open/update on that repo will now trigger an automated code review comment.

---

## Running Tests

All tests run in **mock mode** — no API keys, no Redis, no PostgreSQL required.

```bash
cd backend

# All tests
MOCK_MODE=true pytest -v

# Individual suites
MOCK_MODE=true pytest tests/test_agents.py -v    # Agent node unit tests
MOCK_MODE=true pytest tests/test_review.py -v    # Review API endpoint
MOCK_MODE=true pytest tests/test_problem.py -v   # Problem solver
MOCK_MODE=true pytest tests/test_features.py -v  # CB, cache, webhook, rate limiter

# With coverage report
MOCK_MODE=true pytest --cov=app --cov-report=term-missing
```

**What each test file covers:**

| File | Tests | What's Covered |
|---|---|---|
| `test_agents.py` | 15 | Classifier heuristics, security regex (deterministic), AST complexity, OCR text classification |
| `test_review.py` | 12 | Review endpoint, HTTP status codes, response schema, size limits, request-ID propagation |
| `test_problem.py` | 12 | Problem solver, 4-solution padding, language validation, custom header forwarding |
| `test_features.py` | 18 | Circuit breaker state transitions, rate limiter blocking/open-fail, cache hit counter, GitHub HMAC verification, PR comment formatting, analytics endpoints |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your values. Never commit `.env` to git.

### LLM Providers — add at least one

| Variable | Where to Get | Notes |
|---|---|---|
| `GOOGLE_API_KEY` | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) | Free tier, recommended, best quality |
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) | Very fast, generous free tier |
| `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) | Access to many models |
| `DEEPSEEK_API_KEY` | [platform.deepseek.com](https://platform.deepseek.com/api_keys) | Strong coder model, low cost |
| `MISTRAL_API_KEY` | [console.mistral.ai](https://console.mistral.ai/api-keys) | Codestral model |

### Infrastructure

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Rate limiter + circuit breaker state |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | Task queue broker |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/2` | Task result storage |
| `DATABASE_URL` | `postgresql://reviewer:reviewer@localhost:5432/code_reviewer` | Cache + analytics + tenants |

### GitHub Webhook

| Variable | Description |
|---|---|
| `GITHUB_WEBHOOK_SECRET` | HMAC signing secret (set when creating webhook in GitHub) |
| `GITHUB_TOKEN` | Fine-grained PAT with: pull_requests (read+write), contents (read) |
| `GITHUB_MAX_FILES_PER_PR` | Max files reviewed per PR (default: 10) |

### Circuit Breaker

| Variable | Default | Description |
|---|---|---|
| `CB_FAILURE_THRESHOLD` | `5` | Failures before circuit opens |
| `CB_RECOVERY_TIMEOUT_S` | `30` | Seconds before OPEN → HALF_OPEN |
| `CB_HALF_OPEN_MAX_CALLS` | `2` | Probe calls in HALF_OPEN state |

### Semantic Cache

| Variable | Default | Description |
|---|---|---|
| `CACHE_SIMILARITY_THRESHOLD` | `0.93` | pgvector cosine similarity cutoff |
| `CACHE_TTL_HOURS` | `24` | Cache entry expiry |
| `EMBEDDING_MODEL` | `models/embedding-001` | Google embedding model |

### Rate Limiting

| Variable | Default | Description |
|---|---|---|
| `RATE_LIMIT_FREE_RPM` | `10` | Requests/min for free tier |
| `RATE_LIMIT_PRO_RPM` | `60` | Requests/min for pro tier |
| `RATE_LIMIT_ENTERPRISE_RPM` | `600` | Requests/min for enterprise |
| `DEFAULT_TENANT_API_KEY` | `dev-key-local` | Key used when no header is sent (local dev) |

### Application

| Variable | Default | Description |
|---|---|---|
| `MOCK_MODE` | `false` | Return synthetic responses (for testing, no API keys needed) |
| `MAX_CODE_LENGTH` | `20000` | Character limit for code input |
| `MAX_PROBLEM_LENGTH` | `5000` | Character limit for problem input |
| `LOG_FORMAT` | `json` | `json` for production, `text` for local dev |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |

---

## Project Structure

```
ai-code-reviewer/
├── backend/
│   ├── app/
│   │   ├── main.py                      # FastAPI app, middleware stack, exception handlers
│   │   ├── agents/
│   │   │   ├── graph.py                 # LangGraph DAG: 6 nodes + conditional routing
│   │   │   ├── state.py                 # AgentState TypedDict (shared across all nodes)
│   │   │   └── nodes/
│   │   │       ├── classifier.py        # Language detection + input type classification
│   │   │       ├── security_scanner.py  # 10 regex SAST rules + LLM contextual scan
│   │   │       ├── complexity_analyzer.py # BEFORE baseline (LLM + AST fallback)
│   │   │       ├── optimizer.py         # Full-context code optimisation
│   │   │       ├── validator.py         # Correctness check + automatic rollback
│   │   │       └── explainer.py         # AFTER complexity + developer explanation
│   │   ├── api/v1/routes/
│   │   │   ├── review.py                # Sync + async review, cache integration, metrics
│   │   │   ├── problem.py               # DSA problem solver (4 progressive approaches)
│   │   │   ├── image.py                 # OCR upload → auto-route to review or problem
│   │   │   ├── jobs.py                  # Submit (POST) + poll (GET) async job endpoints
│   │   │   ├── webhooks.py              # GitHub PR webhook: HMAC verify → coordinator task
│   │   │   ├── analytics.py             # 5 analytics endpoints from request_metrics table
│   │   │   └── health.py                # Service health check
│   │   ├── workers/
│   │   │   ├── celery_app.py            # Celery instance: broker, queues, task routing
│   │   │   └── tasks.py                 # review_code_task, solve_problem_task, review_pr_and_comment_task
│   │   ├── services/
│   │   │   ├── llm_service.py           # 5-provider fallback chain + circuit breaker integration
│   │   │   ├── circuit_breaker.py       # CLOSED/OPEN/HALF_OPEN state machine in Redis
│   │   │   ├── cache_service.py         # SHA-256 exact match + pgvector cosine similarity
│   │   │   ├── rate_limiter.py          # Redis sliding window per-tenant
│   │   │   └── ocr_service.py           # Cross-platform Tesseract + 5-step OpenCV preprocessing
│   │   ├── middleware/
│   │   │   ├── auth.py                  # API key → tenant resolution (DB + Redis cache)
│   │   │   └── rate_limiter.py          # FastAPI middleware wrapper
│   │   ├── core/
│   │   │   ├── config.py                # Pydantic Settings — all env vars validated at startup
│   │   │   ├── logging_config.py        # JSON structured logs + request_id ContextVar
│   │   │   ├── exceptions.py            # Domain exceptions → HTTP status codes
│   │   │   └── metrics.py               # Prometheus: 14 counters/histograms/gauges
│   │   ├── db/
│   │   │   └── connection.py            # PG pool + auto schema creation at startup
│   │   ├── models/schemas.py            # All Pydantic v2 request/response shapes
│   │   └── prompts/templates.py         # All 6 LLM prompt templates (versionable)
│   ├── tests/
│   │   ├── conftest.py                  # Shared fixtures, MOCK_MODE bootstrap
│   │   ├── test_review.py               # Review endpoint + security schema
│   │   ├── test_problem.py              # Problem solver edge cases
│   │   ├── test_agents.py               # Node unit tests (no LLM needed)
│   │   └── test_features.py             # CB, rate limiter, cache, webhook
│   ├── Dockerfile                       # Multi-stage: builder → slim runtime, non-root user
│   ├── pytest.ini                       # Test config: asyncio mode, strict markers
│   └── requirements.txt                 # Pinned dependencies
├── frontend/
│   ├── app.py                           # Streamlit: 4 tabs (Review, Problem, Image, History)
│   ├── pages/
│   │   └── 2_Analytics.py               # Live analytics dashboard (polls /analytics/* endpoints)
│   ├── Dockerfile
│   └── requirements.txt
├── streamlit_app.py                     # Standalone: full pipeline inside Streamlit, no FastAPI
├── docker-compose.yml                   # 6 services: postgres, redis, backend, worker, flower, frontend
├── packages.txt                         # Tesseract for Streamlit Cloud auto-install
├── requirements.txt                     # Root-level for Streamlit Cloud
├── .env.example                         # All variables with descriptions + where to get keys
├── .gitignore                           # Covers .env, .venv, __pycache__, secrets.toml, temp/
├── .streamlit/
│   └── config.toml                      # Dark theme, upload size, security settings
├── .github/workflows/ci.yml            # CI: syntax check + tests + Docker build on every push
├── docs/
│   ├── ARCHITECTURE.md                  # Deep system design, node responsibilities, scalability
│   ├── CHANGES.md                       # v1 → v2 → v3 diff with engineering rationale
│   ├── DIAGRAMS.md                      # 3 Mermaid diagrams (render natively on GitHub)
│   └── INTERVIEW_PREP.md                # 30-second pitch + answers to 6 common interview questions
├── DEPLOYMENT.md                        # Step-by-step: Docker, local, Streamlit Cloud
├── LICENSE
└── README.md
```

---

## Tech Stack

| Layer | Technology | Version | Why |
|---|---|---|---|
| Agent orchestration | LangGraph | 0.2.14 | Typed state machine, conditional routing, node isolation |
| API framework | FastAPI | 0.115.0 | Async, Pydantic v2 validation, auto OpenAPI docs |
| Task queue | Celery | 5.4.0 | Decouple submission from processing; survives worker crashes |
| Cache | pgvector + FAISS-style ivfflat | pg16 | Two-tier: exact hash + semantic similarity |
| In-memory store | Redis | 7 | Circuit breaker state, rate limiting, Celery broker/backend |
| Database | PostgreSQL | 16 | Tenants, review cache, request metrics |
| Frontend | Streamlit | 1.37.1 | Fast iteration; multi-page with live analytics |
| OCR | Tesseract + OpenCV | 0.3.10 + 4.10 | Cross-platform; 5-step preprocessing pipeline |
| Observability | prometheus-client | 0.20.0 | 14 metrics; scrape-ready for Grafana/Datadog |
| CI | GitHub Actions | — | Syntax check + tests + Docker build on every push |

---

## Version History

| Version | Focus | Key Additions |
|---|---|---|
| **v1** | Prototype | Single LLM call, Flask, hardcoded Windows Tesseract path, no input validation |
| **v2** | Production | 6-node LangGraph pipeline, security scanner, proper HTTP codes, Pydantic v2, Docker, 30+ tests |
| **v3** | Distributed | Celery async queue, GitHub webhook, pgvector semantic cache, circuit breaker, Prometheus metrics, multi-tenancy, analytics dashboard |

Full engineering rationale for every change: [`docs/CHANGES.md`](docs/CHANGES.md)

---

## Documentation

| Document | Description |
|---|---|
| [Architecture Guide](docs/ARCHITECTURE.md) | Node responsibilities, data flows, scalability analysis, security considerations |
| [Diagrams](docs/DIAGRAMS.md) | Mermaid: full architecture, request lifecycle sequence, security scanner two-layer flow |
| [Change Log](docs/CHANGES.md) | Every v1→v2→v3 change with the engineering rationale behind it |
| [Interview Prep](docs/INTERVIEW_PREP.md) | 30-second pitch + answers to 6 common interview questions about this project |
| [Deployment Guide](DEPLOYMENT.md) | Step-by-step for Docker, local dev, and Streamlit Cloud |

---

## License

MIT — see [LICENSE](LICENSE)

---

<div align="center">
Built with LangGraph · FastAPI · Celery · Redis · PostgreSQL + pgvector · Streamlit
</div>
