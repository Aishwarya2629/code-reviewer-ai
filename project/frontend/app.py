"""
AI Code Reviewer — Streamlit frontend.

Design choices:
- Tabs for the 3 modes (Review / Problem / Image) keep the UI uncluttered.
- Session-state history lets the user scroll back through past reviews.
- Fallback warnings are shown inline — never silently swallowed.
- All API calls go through a single helper that normalises errors into
  the same shape so UI code never has to branch on network errors.
"""
from __future__ import annotations

import os
import io
from typing import Any, Dict, Optional

import requests
import streamlit as st
from PIL import Image

# ── Config ────────────────────────────────────────────────────────────────────
API = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
TIMEOUT = 180   # seconds — complex reviews can take 2–3 LLM round-trips

LANGUAGES = ["auto", "Python", "Java", "JavaScript", "TypeScript", "C++", "Go", "Rust"]

# ── Page setup ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Code Reviewer",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
body { background-color: #0F172A; }

.card {
    background: #1E293B;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 16px;
}
.severity-CRITICAL { color: #EF4444; font-weight: 700; }
.severity-HIGH     { color: #F97316; font-weight: 700; }
.severity-MEDIUM   { color: #EAB308; font-weight: 600; }
.severity-LOW      { color: #22C55E; }
.severity-INFO     { color: #60A5FA; }

.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    margin-right: 6px;
}
.badge-green  { background: #166534; color: #BBF7D0; }
.badge-yellow { background: #713F12; color: #FEF08A; }
.badge-red    { background: #7F1D1D; color: #FECACA; }
.badge-blue   { background: #1E3A5F; color: #BAE6FD; }
</style>
""", unsafe_allow_html=True)


# ── Session state ─────────────────────────────────────────────────────────────
if "history" not in st.session_state:
    st.session_state.history = []   # list of {mode, summary, result}


# ── API helper ────────────────────────────────────────────────────────────────
def call_api(
    endpoint: str,
    json_data: Optional[Dict] = None,
    files: Optional[Dict] = None,
    data: Optional[Dict] = None,
) -> Dict[str, Any]:
    try:
        r = requests.post(f"{API}{endpoint}", json=json_data,
                          files=files, data=data, timeout=TIMEOUT)
        body = r.json()
        if r.status_code not in (200, 201):
            return {"valid": False,
                    "reason": body.get("detail", f"Server error {r.status_code}")}
        return body
    except requests.exceptions.ConnectionError:
        return {"valid": False, "reason": "⚠️ Backend is not running. Start it with: uvicorn app.main:app"}
    except requests.exceptions.Timeout:
        return {"valid": False, "reason": "⏳ Request timed out — the AI took too long. Try again."}
    except Exception as e:
        return {"valid": False, "reason": f"Unexpected error: {e}"}


def check_backend() -> bool:
    try:
        r = requests.get(f"{API}/api/v1/health", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


# ── Shared UI components ──────────────────────────────────────────────────────
def complexity_badge(label: str, value: str):
    colour = "green" if "O(1)" in value or "O(log" in value else \
             "yellow" if "O(n)" in value else \
             "red" if "O(n²)" in value or "O(n^" in value else "blue"
    return f'<span class="badge badge-{colour}">{label}: {value}</span>'


def render_security_issues(issues: list):
    if not issues:
        st.success("✅ No security issues detected")
        return
    st.markdown(f"**{len(issues)} security finding(s)**")
    for issue in issues:
        sev = issue.get("severity", "INFO")
        line = f" (line {issue['line']})" if issue.get("line") else ""
        with st.expander(f"[{sev}] {issue['rule_id']}{line} — {issue['description'][:60]}…"):
            st.markdown(f'<span class="severity-{sev}">Severity: {sev}</span>', unsafe_allow_html=True)
            st.markdown(f"**Description:** {issue['description']}")
            st.info(f"🔧 Recommendation: {issue['recommendation']}")


def render_review(res: Dict, language: str = "python"):
    if not res.get("valid"):
        st.error(f"❌ {res.get('reason', 'Review failed')}")
        return

    # ── Fallback warning ──────────────────────────────────────────────────────
    if res.get("fallback_used"):
        st.warning("⚠️ Primary AI model unavailable — results from fallback provider.")

    col1, col2 = st.columns(2)

    # ── Before / After complexity ─────────────────────────────────────────────
    with col1:
        st.markdown("#### 📊 Before")
        b = res.get("before_complexity", {})
        st.markdown(
            complexity_badge("Time", b.get("time", "O(?)")) +
            complexity_badge("Space", b.get("space", "O(?)")),
            unsafe_allow_html=True,
        )
        if b.get("reasoning"):
            st.caption(b["reasoning"])

    with col2:
        st.markdown("#### 🚀 After")
        a = res.get("after_complexity", {})
        st.markdown(
            complexity_badge("Time", a.get("time", "O(?)")) +
            complexity_badge("Space", a.get("space", "O(?)")),
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Security ─────────────────────────────────────────────────────────────
    st.markdown("#### 🔐 Security Scan")
    render_security_issues(res.get("security_issues", []))
    st.divider()

    # ── Changes ──────────────────────────────────────────────────────────────
    changes = res.get("changes_made", [])
    if changes:
        st.markdown("#### 🛠️ Changes Made")
        for c in changes:
            st.markdown(f"- **[{c.get('category','').upper()}]** {c.get('description')} → _{c.get('impact')}_")
        st.divider()

    # ── Code diff ────────────────────────────────────────────────────────────
    tab_orig, tab_opt = st.tabs(["Original Code", "Optimised Code"])
    with tab_orig:
        st.code(res.get("original_code", ""), language=language.lower())
    with tab_opt:
        if res.get("already_optimal"):
            st.info("✅ Code is already algorithmically optimal. Minor stylistic changes only.")
        st.code(res.get("optimized_code", ""), language=language.lower())

    st.divider()

    # ── Explanation ───────────────────────────────────────────────────────────
    if res.get("analysis"):
        st.markdown("#### 🧠 Analysis")
        st.markdown(res["analysis"])

    if res.get("explanation"):
        st.markdown("#### 💡 Explanation")
        st.markdown(res["explanation"])

    # ── Meta ──────────────────────────────────────────────────────────────────
    meta_cols = st.columns(3)
    meta_cols[0].caption(f"🤖 Provider: `{res.get('provider_used', 'unknown')}`")
    meta_cols[1].caption(f"🆔 Request: `{res.get('request_id', '-')[:8]}…`")
    if res.get("pipeline_ms"):
        meta_cols[2].caption(f"⏱️ {res['pipeline_ms']} ms")


def render_solutions(solutions: list, language: str = "Python", use_expanders: bool = True):
    emojis = ["🐢", "🐇", "⚡", "🏆"]
    colours = ["blue", "yellow", "green", "red"]
    for i, sol in enumerate(solutions):
        emoji = emojis[i] if i < len(emojis) else "📌"
        title = f"{emoji} {sol.get('title', f'Solution {i+1}')}."

        def render_solution_content():
            approach = sol.get("approach", "")
            if approach:
                st.caption(approach)

            tc = sol.get("time_complexity", {})
            sc = sol.get("space_complexity", {})
            st.markdown(
                complexity_badge("Time", tc.get("time", "?")) +
                complexity_badge("Space", sc.get("space", "?")),
                unsafe_allow_html=True,
            )
            if tc.get("reasoning"):
                st.caption(f"Complexity reasoning: {tc['reasoning']}")

            tabs = st.tabs(["Clean Code", "Commented Code"])
            with tabs[0]:
                st.code(sol.get("clean_code", ""), language=language.lower())
            with tabs[1]:
                st.code(sol.get("commented_code", ""), language=language.lower())

        if use_expanders:
            with st.expander(title, expanded=(i == 2)):
                render_solution_content()
        else:
            st.markdown(f"### {title}")
            render_solution_content()


# ── Header ────────────────────────────────────────────────────────────────────
st.title("🔍 AI Code Reviewer")
st.caption("Multi-agent pipeline: Security Scan → Complexity Analysis → Optimization → Explanation")

# Backend status pill
if check_backend():
    st.markdown('<span class="badge badge-green">● Backend Online</span>', unsafe_allow_html=True)
else:
    st.markdown('<span class="badge badge-red">● Backend Offline</span>', unsafe_allow_html=True)
    st.error("Backend is not reachable. Run: `uvicorn app.main:app --reload` in the backend folder.")

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_review, tab_problem, tab_image, tab_history = st.tabs(
    ["📝 Code Review", "🧩 Problem Solver", "🖼️ Image Upload", "📜 History"]
)


# ═══════════════════════════════════════════════════════════════
# TAB 1 — CODE REVIEW
# ═══════════════════════════════════════════════════════════════
with tab_review:
    st.markdown("### Paste your code for a full review")
    st.caption("Runs: Language Detection → Security Scan → Complexity Analysis → Optimization → Validation → Explanation")

    lang_r = st.selectbox("Language", LANGUAGES, key="review_lang")
    code_input = st.text_area(
        "Code", height=300, key="review_code",
        placeholder="Paste your code here…",
    )

    if st.button("🚀 Run Full Review", key="btn_review", type="primary"):
        if not code_input.strip():
            st.warning("Please paste some code first.")
        else:
            with st.spinner("Running 6-node agent pipeline…"):
                res = call_api("/api/v1/review", json_data={
                    "code": code_input, "language": lang_r,
                })
            render_review(res, lang_r if lang_r != "auto" else "python")
            st.session_state.history.insert(0, {
                "mode": "Code Review",
                "summary": code_input[:60] + "…",
                "result": res,
            })


# ═══════════════════════════════════════════════════════════════
# TAB 2 — PROBLEM SOLVER
# ═══════════════════════════════════════════════════════════════
with tab_problem:
    st.markdown("### Describe a DSA problem")
    st.caption("Generates 4 solutions: Brute Force → Better → Optimised → Advanced")

    lang_p = st.selectbox("Language", [l for l in LANGUAGES if l != "auto"], key="prob_lang")
    problem_input = st.text_area(
        "Problem Statement", height=200, key="prob_input",
        placeholder="e.g. Given an array of integers and a target, return the indices of two numbers that add up to target…",
    )

    if st.button("🧩 Solve", key="btn_problem", type="primary"):
        if not problem_input.strip():
            st.warning("Please describe a problem first.")
        elif len(problem_input.strip()) < 10:
            st.warning("Problem statement is too short. Be more descriptive.")
        else:
            with st.spinner("Generating 4 progressive solutions…"):
                res = call_api("/api/v1/problem", json_data={
                    "problem": problem_input, "language": lang_p,
                })
            if not res.get("valid"):
                st.error(res.get("reason", "Something went wrong."))
            else:
                if res.get("fallback_used"):
                    st.warning("⚠️ Fallback provider used.")
                render_solutions(res.get("solutions", []), lang_p)
                st.session_state.history.insert(0, {
                    "mode": "Problem Solver",
                    "summary": problem_input[:60] + "…",
                    "result": res,
                })


# ═══════════════════════════════════════════════════════════════
# TAB 3 — IMAGE UPLOAD
# ═══════════════════════════════════════════════════════════════
with tab_image:
    st.markdown("### Upload a code screenshot or problem image")
    st.caption("Auto-detects whether the image contains code or a problem statement")

    lang_i = st.selectbox("Language hint", LANGUAGES, key="img_lang")
    uploaded = st.file_uploader(
        "Choose an image", type=["png", "jpg", "jpeg", "webp"], key="img_upload"
    )

    if uploaded:
        st.image(Image.open(uploaded), caption="Uploaded image", use_column_width=True)

    if st.button("🖼️ Analyse Image", key="btn_image", type="primary"):
        if not uploaded:
            st.warning("Please upload an image first.")
        else:
            with st.spinner("OCR extraction + AI pipeline running…"):
                uploaded.seek(0)
                res = call_api(
                    "/api/v1/image",
                    files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
                    data={"language": lang_i},
                )

            if not res.get("valid"):
                st.error(res.get("reason", "Image processing failed."))
            else:
                dtype = res.get("detected_type", "unknown")
                st.info(f"Detected as: **{dtype.upper()}**")

                with st.expander("📄 Extracted Text"):
                    st.code(res.get("extracted_text", ""), language="text")

                if dtype == "code" and res.get("review"):
                    render_review(res["review"], lang_i if lang_i != "auto" else "python")
                elif dtype == "problem" and res.get("problem_solutions"):
                    render_solutions(
                        res["problem_solutions"].get("solutions", []), lang_i
                    )
                st.session_state.history.insert(0, {
                    "mode": "Image", "summary": f"Image: {uploaded.name}", "result": res
                })


# ═══════════════════════════════════════════════════════════════
# TAB 4 — HISTORY
# ═══════════════════════════════════════════════════════════════
with tab_history:
    st.markdown("### Review History (this session)")
    if not st.session_state.history:
        st.info("No reviews yet in this session.")
    else:
        if st.button("🗑️ Clear History"):
            st.session_state.history = []
            st.rerun()
        for i, h in enumerate(st.session_state.history):
            with st.expander(f"[{h['mode']}] {h['summary']}", expanded=False):
                if h["mode"] == "Code Review":
                    render_review(h["result"])
                elif h["mode"] == "Problem Solver":
                    render_solutions(h["result"].get("solutions", []), use_expanders=False)
                else:
                    st.json(h["result"])
