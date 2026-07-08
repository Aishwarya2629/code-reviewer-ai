"""
Security scanner node — two-layer approach:

Layer 1: Fast regex-based rules (no LLM cost, < 5ms)
  Catches hardcoded secrets, shell=True, pickle, eval, MD5/SHA1 usage.

Layer 2: LLM-based contextual scan (subtle issues the regex misses)
  Catches SQL injection via f-strings, path traversal, insecure logic.

This dual approach is what distinguishes a production security tool from
a toy — fast rules for obvious issues, LLM for context-sensitive ones.
"""
from __future__ import annotations

import re
from typing import List

from app.agents.state import AgentState, SecurityFinding
from app.core.logging_config import get_logger
from app.prompts.templates import SECURITY_SCAN_PROMPT
from app.services.llm_service import safe_invoke, parse_json_response

logger = get_logger(__name__)


# ── Regex rule definitions ────────────────────────────────────────────────────

_REGEX_RULES: List[tuple] = [
    # (rule_id, severity, pattern, description, recommendation)
    (
        "SEC-001", "CRITICAL",
        r'(?i)(password|passwd|secret|api[_-]?key|token|auth)\s*=\s*["\'][^"\']{4,}["\']',
        "Hardcoded credential or secret detected in source code.",
        "Move secrets to environment variables or a secrets manager (e.g., Vault, AWS Secrets Manager).",
    ),
    (
        "SEC-002", "HIGH",
        r'subprocess\.(?:call|run|Popen)\s*\(.*shell\s*=\s*True',
        "subprocess called with shell=True enables shell injection.",
        "Pass arguments as a list instead: subprocess.run(['cmd', arg]) without shell=True.",
    ),
    (
        "SEC-003", "HIGH",
        r'\bos\.system\s*\(',
        "os.system() passes input directly to the shell — vulnerable to command injection.",
        "Use subprocess.run() with a list of arguments.",
    ),
    (
        "SEC-004", "HIGH",
        r'\beval\s*\(',
        "eval() executes arbitrary code — never call with untrusted input.",
        "Replace with ast.literal_eval() for data, or a purpose-built parser.",
    ),
    (
        "SEC-005", "HIGH",
        r'\bpickle\.loads?\s*\(',
        "pickle.load() deserialises arbitrary Python objects — trivial RCE if source is untrusted.",
        "Use JSON, MessagePack, or protobuf for data exchange.",
    ),
    (
        "SEC-006", "MEDIUM",
        r'\byaml\.load\s*\(',
        "yaml.load() without an explicit Loader can execute arbitrary Python.",
        "Use yaml.safe_load() instead.",
    ),
    (
        "SEC-007", "MEDIUM",
        r'(?i)hashlib\.(?:md5|sha1)\s*\(',
        "MD5/SHA1 are cryptographically broken for password hashing.",
        "Use bcrypt, argon2, or hashlib.sha256 for passwords; SHA256+ for checksums.",
    ),
    (
        "SEC-008", "HIGH",
        r'(?i)(?:execute|cursor\.execute)\s*\(\s*[f"\'].*\{',
        "Possible SQL injection via f-string query construction.",
        "Use parameterised queries: cursor.execute(query, (param,))",
    ),
    (
        "SEC-009", "MEDIUM",
        r'(?i)open\s*\(\s*(?:request|input|params|user|data)',
        "File path derived from user input — potential path traversal.",
        "Sanitise paths with os.path.realpath() and validate against an allowed base directory.",
    ),
    (
        "SEC-010", "LOW",
        r'(?i)print\s*\(.*(?:password|token|secret|key)',
        "Sensitive data may be logged to stdout.",
        "Never log secrets; mask them: '***' or use a structured logger with field filtering.",
    ),
]


def _regex_scan(code: str) -> List[SecurityFinding]:
    findings: List[SecurityFinding] = []
    lines = code.splitlines()

    for rule_id, severity, pattern, description, recommendation in _REGEX_RULES:
        for lineno, line in enumerate(lines, start=1):
            if re.search(pattern, line):
                findings.append({
                    "rule_id": rule_id,
                    "severity": severity,
                    "line": lineno,
                    "description": description,
                    "recommendation": recommendation,
                })
                break  # one finding per rule per file to avoid noise

    return findings


def _llm_scan(code: str, language: str, existing_rule_ids: set) -> List[SecurityFinding]:
    """LLM scan for contextual / subtle issues not caught by regex."""
    try:
        prompt = SECURITY_SCAN_PROMPT.format(language=language, code=code[:8000])
        result = safe_invoke(prompt)
        parsed = parse_json_response(result.content)
        raw_findings = parsed.get("findings", [])

        findings: List[SecurityFinding] = []
        for f in raw_findings:
            rule_id = f.get("rule_id", "LLM-SEC")
            if rule_id in existing_rule_ids:
                continue   # Don't duplicate what regex already found
            severity = f.get("severity", "MEDIUM").upper()
            if severity not in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"):
                severity = "MEDIUM"
            findings.append({
                "rule_id": rule_id,
                "severity": severity,
                "line": f.get("line"),
                "description": f.get("description", ""),
                "recommendation": f.get("recommendation", ""),
            })
        return findings

    except Exception as exc:
        logger.warning(f"LLM security scan failed (non-fatal): {exc}")
        return []


def security_scanner_node(state: AgentState) -> AgentState:
    code = state.get("raw_code", "")
    language = state.get("detected_language", "unknown")
    nodes_executed = list(state.get("nodes_executed", []))
    nodes_executed.append("security_scanner")

    # Layer 1: fast regex
    regex_findings = _regex_scan(code)
    existing_ids = {f["rule_id"] for f in regex_findings}
    logger.info(f"Regex scan: {len(regex_findings)} findings")

    # Layer 2: LLM contextual scan
    llm_findings = _llm_scan(code, language, existing_ids)
    logger.info(f"LLM scan: {len(llm_findings)} additional findings")

    all_findings = regex_findings + llm_findings
    has_critical = any(f["severity"] in ("CRITICAL", "HIGH") for f in all_findings)

    return {
        **state,
        "security_findings": all_findings,
        "has_critical_security": has_critical,
        "nodes_executed": nodes_executed,
    }
