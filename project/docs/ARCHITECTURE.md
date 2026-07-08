# Architecture — AI Code Reviewer v2

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         User (Browser)                               │
└───────────────────────────────┬─────────────────────────────────────┘
                                │  HTTP (port 8501)
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Streamlit Frontend (port 8501)                    │
│  Tab: Code Review | Problem Solver | Image Upload | History          │
└───────────────────────────────┬─────────────────────────────────────┘
                                │  HTTP REST (port 8000)
                                │  X-Request-ID header
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 FastAPI Backend (port 8000)                          │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Middleware Layer                                            │    │
│  │  • Request-ID injection (UUID per request)                  │    │
│  │  • CORS (allow frontend origin)                             │    │
│  │  • Global exception handler (domain errors → HTTP codes)    │    │
│  └────────────────────────┬────────────────────────────────────┘    │
│                           │                                          │
│  ┌────────────────────────▼────────────────────────────────────┐    │
│  │  API Router /api/v1                                          │    │
│  │  POST /review   POST /problem   POST /image   GET /health    │    │
│  └────────────────────────┬────────────────────────────────────┘    │
│                           │                                          │
│  ┌────────────────────────▼────────────────────────────────────┐    │
│  │  LangGraph Agent Pipeline                                    │    │
│  │                                                              │    │
│  │  ┌──────────┐  ┌──────────────┐  ┌───────────────────────┐  │    │
│  │  │Classifier│─▶│Security Scan │─▶│  Complexity Analyzer  │  │    │
│  │  └──────────┘  └──────────────┘  └───────────┬───────────┘  │    │
│  │       │                                       │              │    │
│  │  [invalid]                                    ▼              │    │
│  │       │                             ┌─────────────────┐      │    │
│  │       │                             │    Optimizer    │      │    │
│  │       │                             └────────┬────────┘      │    │
│  │       │                         [invalid]    │               │    │
│  │       │                             │        ▼               │    │
│  │       │                             │  ┌─────────────┐       │    │
│  │       │                             │  │  Validator  │       │    │
│  │       │                             │  └──────┬──────┘       │    │
│  │       │                             │         │               │    │
│  │       │                             │         ▼               │    │
│  │       │                             │  ┌─────────────┐       │    │
│  │       │                             │  │  Explainer  │       │    │
│  │       ▼                             ▼  └──────┬──────┘       │    │
│  │   end_invalid ◀────────────────────────       │               │    │
│  │       └───────────────────────────────────────▼               │    │
│  │                                            END                 │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  LLM Service — Fallback Chain                                │    │
│  │  Priority 1: Gemini 2.5 Pro                                  │    │
│  │  Priority 2: Gemini 2.5 Flash                                │    │
│  │  Priority 3: Groq (Llama 3 70B)                              │    │
│  │  Priority 4: OpenRouter                                      │    │
│  │  Priority 5: DeepSeek Coder                                  │    │
│  │  Priority 6: Mistral Codestral                               │    │
│  │  Fallback:   Mock (always available in MOCK_MODE)            │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  OCR Service (image endpoint only)                           │    │
│  │  OpenCV preprocessing → Tesseract OCR → Text classifier      │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Agent Pipeline — Node Responsibilities

### Node 1: Classifier
- **Input:** Raw code string + requested language
- **Output:** `detected_language`, `input_type` (code/problem/invalid), `confidence`
- **Fallback:** If LLM fails → regex heuristic (pattern matching for `def `, `public class`, etc.)
- **Short-circuit:** If `input_type == "invalid"` → skip all remaining nodes

### Node 2: Security Scanner
- **Input:** Raw code + detected language
- **Output:** List of `SecurityFinding` with rule_id, severity, line, description, recommendation
- **Layer 1 (regex, ~5ms):** 10 deterministic rules — hardcoded secrets, shell injection, eval, pickle, SQL injection, yaml.load, MD5/SHA1, path traversal, secrets in logs
- **Layer 2 (LLM, contextual):** Catches subtle issues that require understanding program flow
- **Non-fatal:** If LLM fails, regex findings still surface

### Node 3: Complexity Analyzer
- **Input:** Raw code + detected language
- **Output:** `before_time`, `before_space`, `reasoning`
- **Establishes the baseline** so the optimizer can compare improvements
- **Fallback:** Python AST traversal counts loop nesting depth → rough heuristic

### Node 4: Optimizer
- **Input:** Raw code + language + before_time/space + security findings summary
- **Output:** `optimized_code`, `already_optimal`, `changes_made`
- **Critical:** Gets full context from previous nodes — language, complexity, and security issues — so the LLM prompt is highly informed
- **Integrity check:** If code is already optimal, sets flag and makes only stylistic improvements. Does not invent changes.

### Node 5: Validator
- **Input:** original code + optimized code + claimed changes
- **Output:** `optimization_valid`, `validator_notes`
- **Rollback:** If validation fails → reverts `optimized_code` to original. The user still gets a useful response, just without an optimization.
- **Skip condition:** If nothing changed (already_optimal), skips to save LLM tokens.

### Node 6: Explainer
- **Input:** Everything from all previous nodes
- **Output:** `after_time`, `after_space`, `analysis`, `explanation`
- **Also calculates:** Post-optimization complexity via a separate LLM call on the optimized code
- Generates developer-facing narrative — not generic text, but contextual explanation of what changed and why

---

## 3. LLM Service Design

### Fallback Chain
```python
# Each call iterates providers in order:
for name, llm in providers:
    try:
        response = llm.invoke(prompt)
        return LLMResult(content, provider_used=name, fallback_used=(idx > 0))
    except:
        sleep(0.5 * idx)   # back-off before next provider
        continue

raise LLMUnavailableError()  # 503 to client
```

### Why this design:
- **No within-provider retry loop** — LangChain already handles transient HTTP errors (max_retries=1). Retrying the same provider wastes time when it's rate-limited.
- **Exponential back-off between providers** — avoids hammering the next provider immediately.
- **MOCK_MODE** short-circuits all network calls — unit tests never touch real APIs.
- **LLMResult value object** — carries latency_ms and fallback_used for observability; routes expose these to clients.

---

## 4. Request Tracing

Every request gets a UUID:
```
Client → [X-Request-ID: abc123] → Middleware → ContextVar → All log lines
                                                          ↓
Client ← [X-Request-ID: abc123] ← Response header
```

This means:
- Clients can correlate frontend errors with backend logs using the same ID
- Log aggregators (Datadog, CloudWatch, ELK) can filter all events for one request
- The UUID is also included in every JSON response body

---

## 5. Security Considerations

| Risk | Mitigation |
|---|---|
| Arbitrary code execution | Removed entirely — complexity via static analysis only |
| Hardcoded secrets | `.env.example` with instructions; `.gitignore` covers `.env` |
| Container running as root | Non-root `appuser` in Dockerfile |
| Unbounded input | `MAX_CODE_LENGTH=20000` enforced before pipeline runs |
| CORS | Explicit origin allowlist from settings, not `*` |
| LLM prompt injection | Code is embedded in fenced blocks; JSON-only responses reduce prose injection |

---

## 6. Scalability Notes (Interview-Ready)

**Current bottleneck:** The 6-node pipeline makes up to 6 sequential LLM calls per review (classifier, security, complexity, optimizer, validator, explainer). At ~1-2s per call, worst-case latency is ~12s.

**Optimisation options if scaling:**
1. **Parallelise independent nodes:** Security scanner and complexity analyzer are fully independent — run them concurrently with `asyncio.gather()`. Saves ~2s.
2. **Cache by code hash:** SHA256 the input; if the same code was reviewed recently, return cached result. Most dev tools see repeated submissions.
3. **Skip validator for low-risk changes:** If optimizer reports `already_optimal=true`, skip validator entirely.
4. **Streaming responses:** Stream the explanation token-by-token to improve perceived latency.
5. **Horizontal scaling:** FastAPI + uvicorn workers are stateless — scale behind a load balancer trivially.

---

## 7. Data Flow for `/api/v1/review`

```
POST /api/v1/review
  { "code": "...", "language": "Python" }
         │
         ▼
   Pydantic validation
   (enum check, min_length, max_length)
         │
         ▼
   run_review_pipeline(code, language, request_id)
         │
         ▼
   AgentState initialised:
   { raw_code, requested_language, request_id,
     nodes_executed=[], security_findings=[], ... }
         │
         ▼
   LangGraph .invoke(state)
   → 6 nodes execute, each returning partial state updates
   → LangGraph merges updates into final state
         │
         ▼
   Final AgentState → ReviewResponse (Pydantic model)
         │
         ▼
   HTTP 200 with JSON body
   X-Request-ID header added
```
