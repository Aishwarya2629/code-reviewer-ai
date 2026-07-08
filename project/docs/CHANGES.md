# CHANGES.md — What Was Improved and Why

This document maps every change from the original project (v1) to the rebuilt version (v2), with the engineering rationale. Useful for interview discussions.

---

## 1. Architecture: Single Node → 6-Node Agent Pipeline

**Before:** One `analyze_node` in `graph.py` that called a single LLM prompt and returned a result.

**After:** A proper LangGraph pipeline:
```
Classifier → SecurityScanner → ComplexityAnalyzer → Optimizer → Validator → Explainer
```

**Why it matters:**
- Each node has **one responsibility** — easier to test, replace, and debug.
- **Conditional routing** (LangGraph edges) short-circuits on invalid input so you don't burn LLM tokens on garbage.
- The **Validator node** is a rollback safety net: if the optimizer produces broken code, we revert to the original and still return a useful response. Without this, a hallucinating LLM silently corrupts the user's code.
- In an interview: this is the difference between a chatbot and an agent.

---

## 2. Security Scanner — New Feature

**Before:** Not present.

**After:** Two-layer scanner:
- **Layer 1 (regex, < 5ms, no LLM cost):** 10 rules covering hardcoded secrets, shell injection, `eval`, `pickle`, SQL injection via f-strings, `yaml.load`, weak hashing, path traversal, secrets in logs.
- **Layer 2 (LLM, contextual):** Catches subtle issues the regex can't — business logic flaws, insecure design patterns.

**Why it matters:**
- Security scanning is the most obvious missing feature in a "code reviewer."
- Regex-first is the right architecture: fast, deterministic, cheap. LLM layer handles subtlety.
- Maps to real SAST tools (Bandit, Semgrep) — shows production awareness.

---

## 3. Hardcoded Windows Path Removed

**Before:**
```python
pytesseract.pytesseract.tesseract_cmd = r'C:\Users\bazzu\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'
```

**After:** Cross-platform discovery in `ocr_service.py`:
1. Check if `tesseract` is on PATH (Linux/macOS — works in Docker)
2. Check common Windows install paths (`C:\Program Files\Tesseract-OCR\`)
3. Log a clear warning if not found — no hard crash

**Why it matters:** This is a P0 bug in any code review. A file with a user-specific path on a Windows machine will crash on every other machine. It would immediately disqualify the project in a code review at any company.

---

## 4. Duplicate Code Eliminated

**Before:** `parse_llm_json()` and `safe_invoke()` were defined in **both** `graph.py` and `main.py` — copy-pasted, slightly different implementations.

**After:** Single source of truth in `app/services/llm_service.py`. Every module imports from there.

**Why it matters:** Duplicate logic means bugs get fixed in one place and silently persist in the other. DRY is a basic software engineering principle.

---

## 5. HTTP Status Codes Fixed

**Before:** Every response — including errors — returned `HTTP 200`.

**After:** Proper status codes via custom exceptions:
| Situation | Status Code |
|---|---|
| Invalid input (empty code, bad format) | `422 Unprocessable Entity` |
| Unsupported language | `400 Bad Request` |
| Input too large | `413 Content Too Large` |
| All LLM providers failed | `503 Service Unavailable` |
| OCR failure | `422 Unprocessable Entity` |
| Unhandled exception | `500 Internal Server Error` |

**Why it matters:** A client (including the frontend, monitoring systems, and load balancers) cannot distinguish success from error if everything is 200. This is HTTP contract design 101.

---

## 6. Pydantic v2 Models with Proper Validation

**Before:** No input validation. Any string could be passed as `language`.

**After:**
- `SupportedLanguage` enum — the API rejects `"COBOL"` with a clear 422 error
- `min_length` on `code` and `problem` fields
- `@field_validator` for additional custom rules
- Proper response models so the API contract is self-documenting in `/docs`

---

## 7. Structured Logging with Request-ID Tracing

**Before:** `print()` statements.

**After:**
- Every log line is a JSON object: `{"ts": "...", "level": "INFO", "request_id": "...", "module": "...", "msg": "..."}`
- A `ContextVar` carries the `request_id` through the entire async call chain
- Request ID is set by middleware, returned in `X-Request-ID` response header, embedded in every log line
- This enables grepping logs by request: `grep "request_id=abc123" app.log`

**Why it matters:** In production, you cannot debug without structured logs. `print()` is not observable.

---

## 8. Arbitrary Code Execution Removed

**Before:** `metrics_runner.py` used `subprocess.run()` to execute user-submitted code and measure its performance.

**After:** Removed entirely. The complexity analyzer uses LLM + AST static analysis instead.

**Why it matters:** Executing arbitrary user code on your server is an **RCE vulnerability**. Any user can submit `import os; os.system("rm -rf /")`. Even with a sandboxed environment (Docker, gVisor), this requires significant security hardening that is out of scope for this project. The right call is to not do it — and static analysis achieves the same goal safely.

---

## 9. API Versioning

**Before:** Routes at `/optimize`, `/problem`, `/image` (no versioning).

**After:** All routes under `/api/v1/...`.

**Why it matters:** Without versioning, any breaking API change requires all clients to update simultaneously. With `v1`, you can introduce `v2` endpoints while keeping `v1` stable for existing clients.

---

## 10. Multi-Stage Docker Build

**Before:** No Dockerfile.

**After:**
- **Stage 1 (builder):** Install all build dependencies and compile Python wheels
- **Stage 2 (runtime):** Copy only the compiled packages — no build tools in production image
- Non-root user (`appuser`) — never run a web service as root
- `HEALTHCHECK` instruction so Docker and orchestrators can verify the service is ready
- `opencv-python-headless` instead of full `opencv-python` — no GUI libs needed in a container (saves ~300MB)

---

## 11. Tests Rewritten as Proper pytest Suite

**Before:** A metrics runner that printed pass/fail counts.

**After:** 30+ pytest tests across 3 files:
- `test_review.py` — HTTP endpoint tests, schema validation, status codes
- `test_problem.py` — problem solver edge cases, padding, propagation
- `test_agents.py` — **unit tests for individual agent nodes with no LLM calls**

The unit tests for agent nodes are particularly valuable: they test the security scanner regex rules deterministically (no LLM needed, no flakiness), the AST heuristic, and the OCR classifier.

---

## 12. OCR Preprocessing Improved

**Before:** Basic grayscale + threshold.

**After:** 5-step pipeline:
1. 2× upscale (improves small-font recognition significantly)
2. Non-local means denoising (preserves edges better than Gaussian blur)
3. Unsharp-mask sharpening
4. Adaptive threshold (handles dark-mode and light-mode editors equally)
5. Character-level cleanup (smart quotes, pipe/I confusion, non-ASCII)

Plus `--psm 6` Tesseract config (treats input as a uniform text block — optimal for code).

---

## Summary Table

| Area | v1 | v2 |
|---|---|---|
| Agent nodes | 1 | 6 |
| Security scanning | ❌ | ✅ (10 regex rules + LLM) |
| Hardcoded user path | ✅ (bug) | ✅ Fixed |
| Duplicate code | ✅ (bug) | ✅ Fixed |
| HTTP status codes | All 200 | Correct per spec |
| Input validation | None | Pydantic v2 enum + validators |
| Structured logging | print() | JSON + request_id |
| Arbitrary code exec | ✅ (RCE risk) | ✅ Removed |
| API versioning | ❌ | ✅ /api/v1/ |
| Docker | ❌ | ✅ Multi-stage, non-root |
| Tests | ad-hoc script | 30+ pytest cases |
| OCR | Basic | 5-step CV pipeline |
