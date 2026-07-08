"""
streamlit_app.py — Standalone entry point for Streamlit Community Cloud.

This file embeds the entire backend pipeline directly inside Streamlit.
No FastAPI, no separate server, no HTTP calls.

How it works:
  1. Reads API keys from st.secrets (Streamlit Cloud) or .env (local)
  2. Directly instantiates the LangGraph pipeline
  3. Calls run_review_pipeline() / solve_problem_direct() in-process
  4. Renders results with the same UI components

Deploy to Streamlit Cloud:
  - Push this repo to GitHub
  - Go to share.streamlit.io → New app → point to streamlit_app.py
  - Add secrets in the Streamlit Cloud dashboard (see .env.example for keys)
"""
from __future__ import annotations

import os
import sys
import json
import re
import time
import uuid
import shutil
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
from PIL import Image

# ── Bootstrap: inject Streamlit secrets into os.environ BEFORE any imports ───
# Streamlit Cloud stores secrets in st.secrets; local dev uses .env
# We normalise both into os.environ so all downstream code works identically.

def _bootstrap_env():
    try:
        for key, val in st.secrets.items():
            if isinstance(val, str) and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass   # st.secrets not available yet or empty — fine for local

_bootstrap_env()

# ── Now safe to import backend code ──────────────────────────────────────────
# Add backend/ to sys.path so we can import app.*
_BACKEND = Path(__file__).parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ─────────────────────────────────────────────────────────────────────────────
# Lazy imports — wrapped so the app shows a friendly error if a dep is missing
# instead of a raw ImportError wall.
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def _load_pipeline():
    """Import and compile the LangGraph pipeline once per process."""
    try:
        from app.agents.graph import run_review_pipeline
        from app.services.llm_service import available_providers, safe_invoke, parse_json_response
        from app.prompts.templates import PROBLEM_PROMPT
        return {
            "run_review": run_review_pipeline,
            "available_providers": available_providers,
            "safe_invoke": safe_invoke,
            "parse_json": parse_json_response,
            "problem_prompt": PROBLEM_PROMPT,
            "ok": True,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@st.cache_resource(show_spinner=False)
def _load_ocr():
    try:
        from app.services.ocr_service import (
            extract_text_from_image, classify_extracted_text, TESSERACT_AVAILABLE
        )
        return {
            "extract": extract_text_from_image,
            "classify": classify_extracted_text,
            "available": TESSERACT_AVAILABLE,
            "ok": True,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "available": False}


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Code Reviewer",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    margin-right: 6px;
}
.badge-green  { background:#166534; color:#BBF7D0; }
.badge-yellow { background:#713F12; color:#FEF08A; }
.badge-red    { background:#7F1D1D; color:#FECACA; }
.badge-blue   { background:#1E3A5F; color:#BAE6FD; }
.badge-gray   { background:#374151; color:#D1D5DB; }
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────
if "history" not in st.session_state:
    st.session_state.history = []

# ── Load pipeline ─────────────────────────────────────────────────────────────
_pipeline = _load_pipeline()
_ocr      = _load_ocr()

if not _pipeline["ok"]:
    st.error(f"❌ Failed to load AI pipeline: {_pipeline.get('error')}")
    st.info("Make sure the `backend/` folder is present and requirements are installed.")
    st.stop()

run_review_pipeline = _pipeline["run_review"]
available_providers = _pipeline["available_providers"]
safe_invoke         = _pipeline["safe_invoke"]
parse_json          = _pipeline["parse_json"]
PROBLEM_PROMPT      = _pipeline["problem_prompt"]

# ── Provider status check ─────────────────────────────────────────────────────
_providers = available_providers()
_mock_mode = os.getenv("MOCK_MODE", "false").lower() == "true"

# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

LANGUAGES = ["auto", "Python", "Java", "JavaScript", "TypeScript", "C++", "Go", "Rust"]

def _complexity_badge(label: str, value: str) -> str:
    colour = (
        "green"  if "O(1)" in value or "O(log" in value else
        "yellow" if value.startswith("O(n)") else
        "red"    if any(x in value for x in ["O(n²)", "O(n^2)", "O(n³)"]) else
        "blue"
    )
    return f'<span class="badge badge-{colour}">{label}: {value}</span>'


def _render_security(issues: list):
    if not issues:
        st.success("✅ No security issues detected")
        return
    sev_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢", "INFO": "🔵"}
    st.markdown(f"**{len(issues)} finding(s) detected**")
    for issue in issues:
        sev  = issue.get("severity", "INFO")
        icon = sev_icon.get(sev, "⚪")
        line = f" — line {issue['line']}" if issue.get("line") else ""
        with st.expander(f"{icon} [{sev}] {issue.get('rule_id','')}{line}"):
            st.markdown(f"**{issue.get('description', '')}**")
            st.info(f"🔧 {issue.get('recommendation', '')}")


def _render_review(result: Dict, language: str = "python"):
    if not result.get("valid"):
        st.error(f"❌ {result.get('explanation', 'Review failed — invalid or unparseable code.')}")
        return

    if result.get("fallback_used"):
        prov = result.get("provider_used", "fallback")
        st.warning(f"⚠️ Primary AI model unavailable — result from **{prov}**.")

    # Complexity before / after
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### 📊 Before")
        b = result.get("before_complexity", {})
        st.markdown(
            _complexity_badge("Time", b.get("time","O(?)")) + " " +
            _complexity_badge("Space", b.get("space","O(?)")),
            unsafe_allow_html=True,
        )
        if b.get("reasoning"):
            st.caption(b["reasoning"])
    with col2:
        st.markdown("#### 🚀 After")
        a = result.get("after_complexity", {})
        st.markdown(
            _complexity_badge("Time", a.get("time","O(?)")) + " " +
            _complexity_badge("Space", a.get("space","O(?)")),
            unsafe_allow_html=True,
        )

    st.divider()

    # Security
    st.markdown("#### 🔐 Security Scan")
    _render_security(result.get("security_issues", []))
    st.divider()

    # Changes
    changes = result.get("changes_made", [])
    if changes:
        st.markdown("#### 🛠️ Changes Made")
        for c in changes:
            cat    = c.get("category", "").upper()
            desc   = c.get("description", "")
            impact = c.get("impact", "")
            st.markdown(f"- **[{cat}]** {desc} → _{impact}_")
        st.divider()

    # Code tabs
    t_orig, t_opt = st.tabs(["Original Code", "Optimised Code"])
    with t_orig:
        st.code(result.get("original_code", ""), language=language.lower())
    with t_opt:
        if result.get("already_optimal"):
            st.info("✅ Already optimal — minor stylistic improvements only.")
        st.code(result.get("optimized_code", ""), language=language.lower())

    st.divider()

    if result.get("analysis"):
        st.markdown("#### 🧠 Analysis")
        st.markdown(result["analysis"])
    if result.get("explanation"):
        st.markdown("#### 💡 Explanation")
        st.markdown(result["explanation"])

    # Meta row
    m1, m2, m3 = st.columns(3)
    m1.caption(f"🤖 Provider: `{result.get('provider_used','—')}`")
    m2.caption(f"🆔 ID: `{result.get('request_id','-')[:8]}…`")
    if result.get("pipeline_ms"):
        m3.caption(f"⏱️ {result['pipeline_ms']} ms")


def _mock_solutions(language: str) -> list:
    return [
        {
            "title": t, "approach": f"{t} approach",
            "clean_code": f"# {t} in {language}\npass",
            "commented_code": f"# {t}\npass",
            "time_complexity": {"time": "O(?)", "space": "O(?)", "reasoning": "Mock"},
            "space_complexity": {"time": "O(?)", "space": "O(?)", "reasoning": "Mock"},
        }
        for t in ("Brute Force", "Better", "Optimised", "Advanced")
    ]


def _solve_problem_direct(problem: str, language: str) -> Dict:
    """Call LLM directly for problem solving — no FastAPI needed."""
    try:
        prompt = PROBLEM_PROMPT.format(language=language, problem=problem)
        result = safe_invoke(prompt, lambda: {"solutions": _mock_solutions(language)})
        parsed = parse_json(result.content)
        raw    = parsed.get("solutions", [])
    except Exception:
        raw    = _mock_solutions(language)
        result = type("R", (), {"provider_used": "mock", "fallback_used": True})()

    # Normalise + pad to 4
    solutions = []
    titles    = ["Brute Force", "Better", "Optimised", "Advanced"]
    for s in raw[:4]:
        tc = s.get("time_complexity", {})
        sc = s.get("space_complexity", {})
        if isinstance(tc, str): tc = {"time": tc, "space": "O(?)", "reasoning": ""}
        if isinstance(sc, str): sc = {"time": "O(?)", "space": sc, "reasoning": ""}
        solutions.append({
            "title":           s.get("title", "Solution"),
            "approach":        s.get("approach", ""),
            "clean_code":      s.get("clean_code", ""),
            "commented_code":  s.get("commented_code", s.get("clean_code", "")),
            "time_complexity": tc,
            "space_complexity": sc,
        })
    while len(solutions) < 4:
        i = len(solutions)
        solutions.append({
            "title": titles[i], "approach": "Unavailable",
            "clean_code": "# Unavailable", "commented_code": "# Unavailable",
            "time_complexity": {"time": "N/A", "space": "N/A", "reasoning": ""},
            "space_complexity": {"time": "N/A", "space": "N/A", "reasoning": ""},
        })

    return {
        "valid": True,
        "solutions": solutions,
        "fallback_used": getattr(result, "fallback_used", False),
        "provider_used": getattr(result, "provider_used", "—"),
    }


def _render_solutions(solutions: list, language: str = "Python"):
    emojis = ["🐢", "🐇", "⚡", "🏆"]
    for i, sol in enumerate(solutions):
        emoji = emojis[i] if i < len(emojis) else "📌"
        with st.expander(f"{emoji} {sol.get('title', f'Solution {i+1}')}", expanded=(i == 2)):
            if sol.get("approach"):
                st.caption(sol["approach"])
            tc = sol.get("time_complexity", {})
            sc = sol.get("space_complexity", {})
            st.markdown(
                _complexity_badge("Time",  tc.get("time",  "?")) + " " +
                _complexity_badge("Space", sc.get("space", "?")),
                unsafe_allow_html=True,
            )
            if tc.get("reasoning"):
                st.caption(f"Why: {tc['reasoning']}")
            t_clean, t_commented = st.tabs(["Clean Code", "Commented Code"])
            with t_clean:
                st.code(sol.get("clean_code", ""), language=language.lower())
            with t_commented:
                st.code(sol.get("commented_code", ""), language=language.lower())


# ─────────────────────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────────────────────
st.title("🔍 AI Code Reviewer")
st.caption("6-node agent pipeline: Classifier → Security Scanner → Complexity → Optimizer → Validator → Explainer")

# Status bar
col_s1, col_s2, col_s3 = st.columns([2, 2, 4])
with col_s1:
    if _providers and not _mock_mode:
        st.markdown(
            f'<span class="badge badge-green">● Live — {_providers[0]}</span>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<span class="badge badge-yellow">● Mock Mode (no API key)</span>',
            unsafe_allow_html=True,
        )
with col_s2:
    ocr_label = "OCR ✅" if _ocr.get("available") else "OCR ❌ (no Tesseract)"
    colour    = "green" if _ocr.get("available") else "red"
    st.markdown(
        f'<span class="badge badge-{colour}">{ocr_label}</span>',
        unsafe_allow_html=True,
    )
with col_s3:
    if not _providers and not _mock_mode:
        st.warning(
            "No API keys found. Add `GOOGLE_API_KEY` (or any provider key) "
            "to `.env` locally or to Streamlit Cloud secrets. "
            "Running in mock mode — responses are synthetic."
        )

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Tabs
# ─────────────────────────────────────────────────────────────────────────────
tab_review, tab_problem, tab_image, tab_history = st.tabs([
    "📝 Code Review", "🧩 Problem Solver", "🖼️ Image Upload", "📜 History"
])


# ═════════════════════════════ TAB 1 — REVIEW ════════════════════════════════
with tab_review:
    st.markdown("### Paste your code for a full review")
    st.caption("Runs the complete 6-node pipeline: security scan, complexity baseline, optimisation, validation, explanation.")

    r_lang = st.selectbox("Language", LANGUAGES, key="r_lang")
    r_code = st.text_area("Code", height=280, key="r_code",
                           placeholder="Paste your code here…")

    if st.button("🚀 Run Full Review", type="primary", key="btn_review"):
        if not r_code.strip():
            st.warning("Please paste some code first.")
        elif len(r_code) > 20_000:
            st.error("Code is too long (max 20,000 characters). Please trim it.")
        else:
            with st.spinner("Running 6-node agent pipeline… (may take 10–30s)"):
                req_id = str(uuid.uuid4())
                t0     = time.perf_counter()
                try:
                    state = run_review_pipeline(
                        code=r_code, language=r_lang, request_id=req_id
                    )
                    elapsed = int((time.perf_counter() - t0) * 1000)

                    # Map AgentState to the flat dict _render_review expects
                    result = {
                        "request_id":     req_id,
                        "valid":          state.get("input_type") != "invalid",
                        "already_optimal": state.get("already_optimal", False),
                        "detected_language": state.get("detected_language", "unknown"),
                        "original_code":  r_code,
                        "optimized_code": state.get("optimized_code", r_code),
                        "before_complexity": {
                            "time":      state.get("before_time", "O(?)"),
                            "space":     state.get("before_space", "O(?)"),
                            "reasoning": state.get("complexity_reasoning", ""),
                        },
                        "after_complexity": {
                            "time":  state.get("after_time", "O(?)"),
                            "space": state.get("after_space", "O(?)"),
                            "reasoning": "",
                        },
                        "security_issues": state.get("security_findings", []),
                        "changes_made":    state.get("changes_made", []),
                        "analysis":        state.get("analysis", ""),
                        "explanation":     state.get("explanation", ""),
                        "fallback_used":   state.get("fallback_used", False),
                        "provider_used":   state.get("provider_used", "—"),
                        "pipeline_ms":     elapsed,
                    }
                except Exception as exc:
                    result = {
                        "valid": False,
                        "explanation": f"Pipeline error: {exc}",
                        "request_id": req_id,
                    }

            _render_review(result, r_lang if r_lang != "auto" else "python")
            st.session_state.history.insert(0, {
                "mode": "Code Review",
                "summary": r_code[:60] + "…",
                "result": result,
            })


# ═════════════════════════ TAB 2 — PROBLEM SOLVER ════════════════════════════
with tab_problem:
    st.markdown("### Describe a DSA problem")
    st.caption("Generates 4 progressive solutions: Brute Force → Better → Optimised → Advanced")

    p_lang = st.selectbox("Language", [l for l in LANGUAGES if l != "auto"], key="p_lang")
    p_prob = st.text_area("Problem Statement", height=200, key="p_prob",
                           placeholder="e.g. Given an array of integers and a target sum, return the indices of the two numbers that add up to the target…")

    if st.button("🧩 Solve", type="primary", key="btn_problem"):
        if not p_prob.strip():
            st.warning("Please describe a problem.")
        elif len(p_prob.strip()) < 10:
            st.warning("Problem statement is too short. Add more detail.")
        elif len(p_prob) > 5000:
            st.error("Problem statement too long (max 5,000 characters).")
        else:
            with st.spinner("Generating 4 progressive solutions…"):
                res = _solve_problem_direct(p_prob, p_lang)

            if res.get("fallback_used"):
                st.warning(f"⚠️ Fallback provider used: {res.get('provider_used')}")

            _render_solutions(res.get("solutions", []), p_lang)
            st.session_state.history.insert(0, {
                "mode": "Problem Solver",
                "summary": p_prob[:60] + "…",
                "result": res,
            })


# ═════════════════════════ TAB 3 — IMAGE UPLOAD ══════════════════════════════
with tab_image:
    st.markdown("### Upload a code screenshot or problem image")
    st.caption("OCR extracts the text, then auto-routes to Code Review or Problem Solver.")

    if not _ocr.get("available"):
        st.error(
            "Tesseract OCR is not installed.\n\n"
            "**Local:** `sudo apt install tesseract-ocr` (Linux) or `brew install tesseract` (macOS)\n\n"
            "**Streamlit Cloud:** `packages.txt` with `tesseract-ocr` is already included in this repo — "
            "it should install automatically on next deploy."
        )
    else:
        i_lang    = st.selectbox("Language hint", LANGUAGES, key="i_lang")
        i_upload  = st.file_uploader("Choose image", type=["png","jpg","jpeg","webp"], key="i_upload")

        if i_upload:
            st.image(Image.open(i_upload), caption="Uploaded image", use_column_width=True)

        if st.button("🖼️ Analyse Image", type="primary", key="btn_image"):
            if not i_upload:
                st.warning("Please upload an image first.")
            else:
                with st.spinner("Running OCR + pipeline…"):
                    tmp_dir  = Path("temp"); tmp_dir.mkdir(exist_ok=True)
                    ext      = Path(i_upload.name).suffix.lower() or ".png"
                    tmp_path = tmp_dir / f"{uuid.uuid4()}{ext}"
                    try:
                        tmp_path.write_bytes(i_upload.getvalue())
                        text = _ocr["extract"](str(tmp_path))
                    finally:
                        try: tmp_path.unlink(missing_ok=True)
                        except: pass

                if not text.strip():
                    st.error("Could not extract text from the image. Ensure the image is clear and well-lit.")
                else:
                    dtype = _ocr["classify"](text)
                    st.info(f"Detected as: **{dtype.upper()}**")
                    with st.expander("📄 Extracted Text"):
                        st.code(text, language="text")

                    req_id = str(uuid.uuid4())
                    if dtype == "code":
                        with st.spinner("Running code review pipeline…"):
                            state = run_review_pipeline(
                                code=text,
                                language=i_lang if i_lang != "auto" else "auto",
                                request_id=req_id,
                            )
                            result = {
                                "request_id": req_id,
                                "valid": state.get("input_type") != "invalid",
                                "already_optimal": state.get("already_optimal", False),
                                "detected_language": state.get("detected_language", "unknown"),
                                "original_code": text,
                                "optimized_code": state.get("optimized_code", text),
                                "before_complexity": {"time": state.get("before_time","O(?)"),"space": state.get("before_space","O(?)"),"reasoning": ""},
                                "after_complexity":  {"time": state.get("after_time","O(?)"),"space": state.get("after_space","O(?)"),"reasoning": ""},
                                "security_issues": state.get("security_findings", []),
                                "changes_made": state.get("changes_made", []),
                                "analysis": state.get("analysis",""),
                                "explanation": state.get("explanation",""),
                                "fallback_used": state.get("fallback_used", False),
                                "provider_used": state.get("provider_used","—"),
                                "pipeline_ms": None,
                            }
                        _render_review(result, i_lang if i_lang != "auto" else "python")
                    else:
                        with st.spinner("Solving problem…"):
                            res = _solve_problem_direct(text, i_lang if i_lang != "auto" else "Python")
                        _render_solutions(res.get("solutions",[]), i_lang if i_lang != "auto" else "Python")


# ════════════════════════════ TAB 4 — HISTORY ════════════════════════════════
with tab_history:
    st.markdown("### Session History")
    if not st.session_state.history:
        st.info("No reviews yet this session.")
    else:
        if st.button("🗑️ Clear History"):
            st.session_state.history = []
            st.rerun()
        for h in st.session_state.history:
            with st.expander(f"[{h['mode']}] {h['summary']}"):
                if h["mode"] == "Code Review":
                    _render_review(h["result"])
                elif h["mode"] == "Problem Solver":
                    _render_solutions(h["result"].get("solutions",[]))
                else:
                    st.json(h["result"])
