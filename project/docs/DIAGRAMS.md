# Architecture Diagram

> Paste the block below into https://mermaid.live to render it, or view it directly on GitHub (renders automatically in `.md` files).

```mermaid
graph TB
    U([👤 User]) --> FE[Streamlit Frontend<br/>port 8501]
    FE -->|HTTP REST| MW

    subgraph Backend ["⚙️ FastAPI Backend — port 8000"]
        MW[Middleware<br/>Request-ID · CORS · Exception Handler]
        MW --> RT[API Router /api/v1]

        RT -->|POST /review| P1[run_review_pipeline]
        RT -->|POST /problem| P2[solve_problem]
        RT -->|POST /image| P3[image_to_review]
        RT -->|GET /health| P4[health_check]

        P3 -->|OCR| OCR[OCR Service<br/>OpenCV preprocessing<br/>Tesseract extraction<br/>Text classifier]
        OCR -->|code| P1
        OCR -->|problem| P2

        subgraph Pipeline ["🤖 LangGraph Agent Pipeline"]
            N1[1. Classifier<br/>Language detection<br/>Input type check]
            N2[2. Security Scanner<br/>Regex SAST Layer 1<br/>LLM contextual Layer 2]
            N3[3. Complexity Analyzer<br/>Before baseline<br/>AST fallback]
            N4[4. Optimizer<br/>Full-context prompt<br/>Informed by all above]
            N5[5. Validator<br/>Rollback if broken]
            N6[6. Explainer<br/>After complexity<br/>Narrative generation]
            EINV[end_invalid]

            N1 -->|valid| N2
            N1 -->|invalid| EINV
            N2 --> N3
            N3 --> N4
            N4 -->|valid| N5
            N4 -->|invalid| EINV
            N5 --> N6
        end

        P1 --> Pipeline

        subgraph LLMSvc ["🔗 LLM Service — Fallback Chain"]
            G1[Gemini 2.5 Pro]
            G2[Gemini 2.5 Flash]
            GR[Groq LLaMA3]
            OR[OpenRouter]
            DS[DeepSeek]
            MIS[Mistral]
            MK[Mock fallback]

            G1 -->|fail| G2
            G2 -->|fail| GR
            GR -->|fail| OR
            OR -->|fail| DS
            DS -->|fail| MIS
            MIS -->|fail| MK
        end

        Pipeline --> LLMSvc
    end

    style Pipeline fill:#1e293b,stroke:#7c3aed,color:#e2e8f0
    style LLMSvc fill:#1e293b,stroke:#059669,color:#e2e8f0
    style Backend fill:#0f172a,stroke:#334155,color:#e2e8f0
```

---

# Workflow Diagram — Request Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant FE as Streamlit Frontend
    participant MW as Middleware
    participant API as /api/v1/review
    participant CL as Classifier Node
    participant SS as Security Scanner
    participant CA as Complexity Analyzer
    participant OP as Optimizer
    participant VA as Validator
    participant EX as Explainer
    participant LLM as LLM Service

    User->>FE: Paste code + click Review
    FE->>MW: POST /api/v1/review {code, language}
    MW->>MW: Generate UUID request_id
    MW->>MW: Set ContextVar(request_id)
    MW->>API: Forward with request.state.request_id

    API->>API: Pydantic validation (length, enum)
    Note over API: 422 if invalid input<br/>413 if too large

    API->>CL: invoke pipeline
    CL->>LLM: Classify language + type
    LLM-->>CL: {detected_language, input_type}

    alt input_type == "invalid"
        CL-->>API: end_invalid state
        API-->>FE: 422 {valid: false}
    else input_type == "code"
        CL->>SS: Pass state
        SS->>SS: Regex scan (10 rules, ~5ms)
        SS->>LLM: LLM contextual scan
        LLM-->>SS: Additional findings
        SS->>CA: Pass state + findings

        CA->>LLM: Analyse complexity of original
        LLM-->>CA: before_time, before_space

        CA->>OP: Pass state + complexity baseline
        OP->>LLM: Optimise (with full context)
        LLM-->>OP: optimized_code, changes_made

        OP->>VA: Pass state
        VA->>LLM: Validate optimization
        LLM-->>VA: {valid, notes}

        alt optimization invalid
            VA->>VA: Rollback to original code
        end

        VA->>EX: Pass state
        EX->>LLM: Get after complexity
        EX->>LLM: Generate explanation
        LLM-->>EX: analysis, explanation

        EX-->>API: Final AgentState
        API-->>MW: ReviewResponse (Pydantic)
        MW-->>FE: HTTP 200 + X-Request-ID header
        FE-->>User: Render results
    end
```

---

# Security Scanner — Two-Layer Design

```mermaid
flowchart TD
    IN[Code Input] --> R1

    subgraph Layer1 ["Layer 1: Regex SAST — deterministic, ~5ms, $0 LLM cost"]
        R1[SEC-001: Hardcoded secrets]
        R2[SEC-002: subprocess shell=True]
        R3[SEC-003: os.system]
        R4[SEC-004: eval with input]
        R5[SEC-005: pickle.loads]
        R6[SEC-006: yaml.load unsafe]
        R7[SEC-007: MD5/SHA1 passwords]
        R8[SEC-008: SQL f-string injection]
        R9[SEC-009: Path traversal]
        R10[SEC-010: Secrets in logs]
    end

    subgraph Layer2 ["Layer 2: LLM Contextual Scan — catches subtle issues"]
        L1[Business logic flaws]
        L2[Insecure design patterns]
        L3[Context-dependent injections]
        L4[Race conditions]
    end

    R1 & R2 & R3 & R4 & R5 & R6 & R7 & R8 & R9 & R10 --> MERGE
    Layer2 --> MERGE
    MERGE[Deduplicate findings] --> OUT[SecurityFindings list<br/>rule_id · severity · line · description · recommendation]

    style Layer1 fill:#1e1e2e,stroke:#ef4444,color:#fca5a5
    style Layer2 fill:#1e1e2e,stroke:#f97316,color:#fed7aa
```
