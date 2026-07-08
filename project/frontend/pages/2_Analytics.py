"""
Analytics Dashboard — real-time metrics pulled from /api/v1/analytics/*.

Shows:
  - KPI tiles (total requests, cache hit rate, error rate, p95 latency)
  - Requests over time (area chart)
  - Provider usage + circuit breaker states (bar chart + table)
  - Security findings summary
  - Language distribution (pie)
  - Latency percentiles per endpoint (table)

Auto-refreshes every 30s when the toggle is on.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict

import requests
import streamlit as st

API = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Analytics Dashboard", page_icon="📊", layout="wide")

st.title("📊 Analytics Dashboard")
st.caption("Live metrics from the AI Code Reviewer backend")

# ── Controls ──────────────────────────────────────────────────────────────────
col_h, col_r, col_a = st.columns([2, 1, 1])
with col_h:
    hours = st.select_slider("Time window", options=[1, 6, 12, 24, 48, 168],
                              value=24, format_func=lambda h: f"Last {h}h")
with col_r:
    auto_refresh = st.toggle("Auto-refresh (30s)", value=False)
with col_a:
    if st.button("🔄 Refresh now"):
        st.rerun()


def _get(path: str) -> Dict[str, Any]:
    try:
        r = requests.get(f"{API}/api/v1/analytics/{path}?hours={hours}", timeout=10)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


# ── Fetch all data in parallel (sequentially for simplicity) ─────────────────
with st.spinner("Loading metrics…"):
    overview  = _get("overview")
    timeline  = _get("requests-over-time")
    providers = _get("providers")
    security  = _get("security")
    languages = _get("languages")
    latency   = _get("latency")

st.divider()

# ── KPI Tiles ─────────────────────────────────────────────────────────────────
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Total Requests",    overview.get("total_requests", "—"))
k2.metric("Cache Hit Rate",    f"{overview.get('cache_hit_rate', 0):.1f}%")
k3.metric("Error Rate",        f"{overview.get('error_rate', 0):.1f}%",
          delta_color="inverse")
k4.metric("Avg Latency",       f"{overview.get('avg_latency_ms', 0):.0f} ms")
k5.metric("p95 Latency",       f"{overview.get('p95_latency_ms', 0):.0f} ms")
k6.metric("Security Issues",   overview.get("requests_with_security_issues", "—"),
          help="Requests that had ≥1 security finding")

st.divider()

# ── Requests Over Time ────────────────────────────────────────────────────────
st.subheader("📈 Requests Over Time")
if timeline:
    import pandas as pd
    df = pd.DataFrame(timeline)
    if not df.empty and "hour" in df.columns:
        df["hour"] = pd.to_datetime(df["hour"])
        df = df.set_index("hour")
        st.area_chart(df[["requests", "cache_hits", "errors"]],
                      color=["#4285F4", "#34A853", "#EA4335"])
    else:
        st.info("No data yet for this time window.")
else:
    st.info("No timeline data available (backend may be running without a database).")

st.divider()

# ── Provider Health + Circuit Breakers ───────────────────────────────────────
col_prov, col_cb = st.columns([3, 2])

with col_prov:
    st.subheader("🤖 Provider Usage")
    usage = providers.get("usage", [])
    if usage:
        import pandas as pd
        df_p = pd.DataFrame(usage)
        st.bar_chart(df_p.set_index("provider")[["requests"]])
    else:
        st.info("No provider data yet.")

with col_cb:
    st.subheader("⚡ Circuit Breakers")
    cb_states = providers.get("circuit_breakers", {})
    if cb_states:
        state_emoji = {"CLOSED": "🟢", "HALF_OPEN": "🟡", "OPEN": "🔴"}
        for prov, state in cb_states.items():
            emoji = state_emoji.get(state, "⚪")
            st.markdown(f"{emoji} **{prov}** — `{state}`")
    else:
        st.success("🟢 All providers: CLOSED (healthy)")
        st.caption("Circuit breaker states appear here once requests are made")

st.divider()

# ── Security + Languages ──────────────────────────────────────────────────────
col_sec, col_lang = st.columns(2)

with col_sec:
    st.subheader("🔐 Security Findings")
    total_issues   = security.get("total_issues", 0)
    affected       = security.get("affected_reviews", 0)
    total_reviews  = security.get("total_reviews", 0)

    s1, s2, s3 = st.columns(3)
    s1.metric("Total Issues",      total_issues)
    s2.metric("Affected Reviews",  affected)
    s3.metric("Clean Reviews",     max(0, total_reviews - affected))

    if total_reviews > 0:
        pct = affected / total_reviews * 100
        st.progress(int(pct), text=f"{pct:.1f}% of reviews had security findings")

with col_lang:
    st.subheader("🌐 Language Distribution")
    if languages:
        import pandas as pd
        df_l = pd.DataFrame(languages)
        if not df_l.empty:
            st.bar_chart(df_l.set_index("language"))
    else:
        st.info("No language data yet.")

st.divider()

# ── Latency Table ─────────────────────────────────────────────────────────────
st.subheader("⏱️ Latency Percentiles by Endpoint")
if latency:
    import pandas as pd
    df_lat = pd.DataFrame(latency)
    df_lat.columns = ["Endpoint", "p50 (ms)", "p95 (ms)", "p99 (ms)", "Requests"]
    st.dataframe(df_lat, use_container_width=True, hide_index=True)
else:
    st.info("No latency data yet.")

# ── Provider detail table ─────────────────────────────────────────────────────
if providers.get("usage"):
    st.subheader("Provider Detail")
    import pandas as pd
    df_pd = pd.DataFrame(providers["usage"])
    df_pd.columns = ["Provider", "Requests", "Avg ms", "CB State"]
    st.dataframe(df_pd, use_container_width=True, hide_index=True)

# ── Auto-refresh ──────────────────────────────────────────────────────────────
if auto_refresh:
    time.sleep(30)
    st.rerun()
