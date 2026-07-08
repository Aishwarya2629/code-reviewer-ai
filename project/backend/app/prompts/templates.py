"""
All LLM prompt templates in one place.

Design decision: templates are plain strings with {format_placeholders},
not LangChain PromptTemplate objects. This keeps them readable, diffable in
git, and testable without importing LangChain.

Each prompt ends with explicit JSON schema + rules to minimise hallucination.
"""

# ── Classifier ────────────────────────────────────────────────────────────────

CLASSIFIER_PROMPT = """\
You are a programming language detector and input classifier.

INPUT:
```
{code}
```

Determine:
1. The programming language (Python, Java, JavaScript, TypeScript, C++, Go, Rust, or "unknown")
2. Whether the input is valid source code ("code"), a problem statement ("problem"), or neither ("invalid")
3. Your confidence as a float between 0.0 and 1.0

Respond ONLY with valid JSON. No markdown, no prose.

{{
  "detected_language": "<language or unknown>",
  "input_type": "<code|problem|invalid>",
  "confidence": <0.0-1.0>,
  "reason": "<one sentence>"
}}
"""


# ── Security scanner ──────────────────────────────────────────────────────────

SECURITY_SCAN_PROMPT = """\
You are a senior application security engineer performing a SAST (static analysis) review.

Language: {language}
Code:
```
{code}
```

Scan for the following vulnerability categories:
- Hardcoded secrets / credentials / API keys
- SQL injection (string-formatted queries)
- Command injection (os.system, subprocess with shell=True, exec, eval with untrusted input)
- Path traversal (unvalidated file paths from user input)
- Insecure deserialization (pickle, yaml.load without Loader)
- Weak cryptography (MD5, SHA1 for passwords, custom crypto)
- Sensitive data exposure (PII in logs, unmasked secrets in output)
- Infinite loops / resource exhaustion (unbounded recursion, no timeout)

Return ONLY valid JSON. No markdown. No prose outside the JSON.
If no issues found, return an empty findings array.

{{
  "findings": [
    {{
      "rule_id": "<CWE-XXX or descriptive id>",
      "severity": "<CRITICAL|HIGH|MEDIUM|LOW|INFO>",
      "line": <line_number or null>,
      "description": "<what the issue is>",
      "recommendation": "<specific fix in 1-2 sentences>"
    }}
  ]
}}
"""


# ── Complexity analyzer ───────────────────────────────────────────────────────

COMPLEXITY_PROMPT = """\
You are an algorithm analysis expert (PhD-level).

Language: {language}
Code:
```
{code}
```

Analyse the time and space complexity of the ORIGINAL code as written.
Use standard Big-O notation. If multiple cases apply, list best/average/worst.

Respond ONLY with valid JSON. No markdown.

{{
  "time_complexity": "<e.g. O(n log n)>",
  "space_complexity": "<e.g. O(n)>",
  "reasoning": "<2-3 sentences explaining the analysis>"
}}
"""


# ── Optimizer ─────────────────────────────────────────────────────────────────

OPTIMIZER_PROMPT = """\
You are a Google L6 software engineer performing a code review.

Language: {language}
Original Time Complexity: {before_time}
Original Space Complexity: {before_space}
Security Issues Found: {security_summary}

Code:
```
{code}
```

STRICT RULES:
1. Preserve the SAME language unless explicitly instructed otherwise.
2. Only claim "optimized" if there is a REAL, measurable improvement in at least one of:
   time complexity | space complexity | memory | readability | idiomatic correctness
3. If the code is already algorithmically optimal, set "already_optimal": true and only
   apply idiomatic / readability improvements (rename variables, use language idioms).
4. DO NOT generate a different algorithm just to appear helpful.
5. Fix any CRITICAL or HIGH security issues found.
6. If the code is invalid, incomplete, or gibberish, set "valid": false.

Respond ONLY with valid JSON. No markdown. No prose outside the JSON.

{{
  "valid": true,
  "already_optimal": false,
  "optimized_code": "<full optimized source>",
  "changes_made": [
    {{
      "category": "<algorithmic|readability|memory|security|idiomatic>",
      "description": "<what changed>",
      "impact": "<measurable impact, e.g. O(n²) → O(n)>"
    }}
  ],
  "reason_if_invalid": null
}}
"""


# ── Validator ─────────────────────────────────────────────────────────────────

VALIDATOR_PROMPT = """\
You are a senior code reviewer validating an optimization.

Language: {language}
Original code:
```
{original_code}
```

Proposed optimized code:
```
{optimized_code}
```

Check:
1. Does the optimized code compile/parse correctly (no obvious syntax errors)?
2. Does it preserve the original intent and return values?
3. Are the claimed changes (below) actually present?

Claimed changes:
{changes_summary}

Respond ONLY with valid JSON.

{{
  "valid": true,
  "notes": "<brief reviewer note, or 'LGTM' if no issues>"
}}
"""


# ── Explainer ─────────────────────────────────────────────────────────────────

EXPLAINER_PROMPT = """\
You are a technical writer and educator.

Language: {language}
Original complexity: time={before_time}, space={before_space}
Optimized complexity: time={after_time}, space={after_space}
Already optimal: {already_optimal}
Changes made: {changes_summary}
Security issues addressed: {security_summary}

Write a clear, developer-friendly explanation of:
1. What was changed and why
2. The algorithmic / design reasoning
3. What the developer should learn from this

Keep it to 3-5 paragraphs. Be concrete, not generic.
Do NOT repeat the code.
Return ONLY valid JSON.

{{
  "analysis": "<technical analysis of the original code>",
  "explanation": "<developer-facing explanation of changes and learnings>"
}}
"""


# ── Problem solver ────────────────────────────────────────────────────────────

PROBLEM_PROMPT = """\
You are a Google-level DSA mentor and competitive programmer.

Language: {language}
Problem:
{problem}

Generate EXACTLY 4 solutions in order of sophistication:
1. Brute Force — naive, clearly correct, no optimisation
2. Better — one key insight applied (e.g. sorting, HashMap)
3. Optimised — best practical time complexity for most interview settings
4. Advanced — best known complexity or elegant interview-ready variation

Rules:
- All solutions in {language} only
- clean_code: production-ready, no comments
- commented_code: same code with inline explanation comments
- Be precise with complexity — justify it in 1 sentence
- No placeholder code

Respond ONLY with valid JSON. No markdown.

{{
  "solutions": [
    {{
      "title": "<Brute Force|Better|Optimised|Advanced>",
      "approach": "<1-sentence approach summary>",
      "clean_code": "<full code, newlines as \\n>",
      "commented_code": "<same code with comments>",
      "time_complexity": {{
        "time": "<Big-O>",
        "space": "<Big-O>",
        "reasoning": "<why>"
      }},
      "space_complexity": {{
        "time": "<Big-O>",
        "space": "<Big-O>",
        "reasoning": "<why>"
      }}
    }}
  ]
}}
"""
