"""
🔍 XAI Explorer
=================
Explainability dashboard — browse past queries, view full agent traces,
inspect ChromaDB retrieval results, and examine SQL queries.
"""

import os
import json
from pathlib import Path
import streamlit as st
import pandas as pd
import requests

st.set_page_config(page_title="XAI Explorer — Proviso", page_icon="🔍", layout="wide")

FRONTEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = (
    FRONTEND_ROOT.parent
    if (FRONTEND_ROOT.parent / "data").exists()
    else FRONTEND_ROOT
)
DATA_DIR = PROJECT_ROOT / "data"
API_URL = os.getenv("BACKEND_API_URL", "http://localhost:8000")

# ── Header ─────────────────────────────────────────────────────────
st.markdown("""
<style>
    .xai-header {
        background: linear-gradient(135deg, #a18cd1 0%, #fbc2eb 100%);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
    }
    .xai-header h2 { color: #1a1a2e; margin: 0; }
    .xai-header p { color: rgba(26,26,46,0.8); margin: 0.3rem 0 0 0; }
    .trace-step {
        background: #1a1a2e;
        border-left: 3px solid #667eea;
        padding: 0.8rem 1rem;
        margin: 0.5rem 0;
        border-radius: 0 8px 8px 0;
    }
</style>
<div class="xai-header">
    <h2>🔍 Explainability (XAI) Explorer</h2>
    <p>Full transparency: inspect agent reasoning, source documents, SQL queries, and decision traces</p>
</div>
""", unsafe_allow_html=True)

# ── Tabs ───────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "🧠 Agent Architecture",
    "📄 Contract Index",
    "🗃️ Query History",
    "📊 Feedback Analytics"
])

# ── Tab 1: Agent Architecture ─────────────────────────────────────
with tab1:
    st.markdown("### Multi-Agent System Architecture")
    st.markdown("""
    The system uses a **LangGraph StateGraph** with a **Supervisor Pattern** to orchestrate
    two specialist agents. Each query goes through these steps:
    """)

    st.markdown("""
    ```mermaid
    graph TD
        A[User Query] --> B[Supervisor Node]
        B -->|Contract/Compliance| C[Legal Agent - RAG]
        B -->|Payment/Invoice| D[Finance Agent - SQL]
        B -->|Both needed| C
        C --> B
        D --> B
        B -->|All info gathered| E[Final Report]
        C --> F[(ChromaDB)]
        D --> G[(PostgreSQL)]
        E --> H[User Response + XAI]
    ```
    """)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### ⚖️ Legal Agent (RAG)")
        st.markdown("""
        - **Role**: Compliance Auditor
        - **Data**: 50 vendor contracts (PDFs)
        - **Vector DB**: ChromaDB
        - **Embeddings**: `all-mpnet-base-v2`
        - **Chunking**: Parent-Child Recursive
        - **Output**: Findings + Source Citations

        **Detects:**
        - Missing GDPR clauses
        - High penalty terms (>10%)
        - Weak termination protections
        """)

    with col2:
        st.markdown("#### 💰 Finance Agent (SQL)")
        st.markdown("""
        - **Role**: Financial Analyst
        - **Data**: 10,000+ invoice records
        - **Database**: PostgreSQL (read-only)
        - **Method**: Text-to-SQL generation
        - **Safety**: Keyword blocklist + retry loop
        - **Output**: Query results + SQL shown

        **Detects:**
        - Overpayments vs contract cap
        - Duplicate invoices
        - Overdue payment clusters
        """)

    st.markdown("---")
    st.markdown("### 🔒 Security Guardrails")
    st.info("""
    **SQL Injection Prevention**: All agent-generated SQL is validated against a blocklist
    (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `TRUNCATE`). Only `SELECT` queries are permitted.

    **Authentication**: JWT tokens (HS256) with 30-minute expiry. Refresh token rotation.

    **Rate Limiting**: 30 requests/minute per user via SlowAPI.

    **Read-Only DB User**: The Finance Agent connects via `readonly_agent` PostgreSQL role
    with only `SELECT` privileges.
    """)

# ── Tab 2: Contract Index ─────────────────────────────────────────
with tab2:
    st.markdown("### 📄 Indexed Contract Documents")

    metadata_file = DATA_DIR / "contracts_metadata.json"
    if metadata_file.exists():
        with open(metadata_file) as f:
            contracts = json.load(f)

        df = pd.DataFrame(contracts)

        # Filters
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            type_filter = st.multiselect(
                "Contract Type",
                df["contract_type"].unique().tolist(),
                default=df["contract_type"].unique().tolist()
            )
        with fc2:
            gdpr_filter = st.selectbox("GDPR Status", ["All", "Compliant", "Missing"])
        with fc3:
            risk_filter = st.multiselect(
                "Risk Tier",
                df["risk_tier"].unique().tolist() if "risk_tier" in df.columns else [],
                default=df["risk_tier"].unique().tolist() if "risk_tier" in df.columns else []
            )

        filtered = df[df["contract_type"].isin(type_filter)]
        if gdpr_filter == "Compliant":
            filtered = filtered[filtered["include_gdpr"] == True]
        elif gdpr_filter == "Missing":
            filtered = filtered[filtered["include_gdpr"] == False]
        if risk_filter and "risk_tier" in df.columns:
            filtered = filtered[filtered["risk_tier"].isin(risk_filter)]

        display_cols = ["vendor_id", "vendor_name", "country", "contract_type",
                        "monthly_cap", "late_fee_pct", "include_gdpr", "risk_tier"]
        available_cols = [c for c in display_cols if c in filtered.columns]

        st.dataframe(
            filtered[available_cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "monthly_cap": st.column_config.NumberColumn("Monthly Cap (INR)", format="₹%d"),
                "late_fee_pct": st.column_config.NumberColumn("Late Fee %", format="%d%%"),
                "include_gdpr": st.column_config.CheckboxColumn("GDPR"),
            }
        )

        st.caption(f"Showing {len(filtered)} of {len(df)} contracts")
    else:
        st.warning("Contract metadata not found.")

# ── Tab 3: Query History ──────────────────────────────────────────
with tab3:
    st.markdown("### 🗃️ Recent Queries & Agent Traces")

    if "chat_history" in st.session_state and st.session_state.chat_history:
        for i, msg in enumerate(reversed(st.session_state.chat_history)):
            if msg["role"] == "user":
                with st.expander(f"🧑‍💼 Query: {msg['content'][:80]}...", expanded=(i < 2)):
                    st.markdown(f"**Query:** {msg['content']}")

                    # Find corresponding response
                    idx = len(st.session_state.chat_history) - 1 - i
                    if idx + 1 < len(st.session_state.chat_history):
                        resp = st.session_state.chat_history[idx + 1]
                        if resp["role"] == "assistant":
                            st.markdown(f"**Response:** {resp['content'][:500]}")

                            if "metadata" in resp:
                                meta = resp["metadata"]
                                if meta.get("sql_queries"):
                                    st.markdown("**SQL Queries:**")
                                    for q in meta["sql_queries"]:
                                        st.code(q, language="sql")
                                if meta.get("cited_sources"):
                                    st.markdown("**Sources:**")
                                    for s in meta["cited_sources"]:
                                        st.markdown(f"- {s.get('filename', '?')} p.{s.get('page_number', '?')}")
                                st.caption(f"Iterations: {meta.get('iterations', '?')}")
    else:
        st.info("No queries yet. Go to the Chat page to start querying the agents.")

# ── Tab 4: Feedback Analytics ─────────────────────────────────────
with tab4:
    st.markdown("### 📊 User Feedback (RLHF Loop)")

    token = st.session_state.get("auth_token")
    if not token:
        st.info("Log in on the Proviso home page to view persisted feedback.")
    else:
        try:
            response = requests.get(
                f"{API_URL}/feedback",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            response.raise_for_status()
            records = response.json().get("records", [])
        except requests.RequestException:
            records = None
            st.error("Persisted feedback could not be loaded from the API.")

        if records:
            fb_df = pd.DataFrame(records)
            avg_rating = fb_df["rating"].mean()

            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                st.metric("Total Feedback", len(records))
            with fc2:
                st.metric("Average Rating", f"{avg_rating:.1f}/5.0")
            with fc3:
                st.metric("Positive (4-5)", len(fb_df[fb_df["rating"] >= 4]))

            # Rating distribution
            if len(fb_df) > 0:
                import plotly.express as px
                fig = px.histogram(fb_df, x="rating", nbins=5,
                                   color_discrete_sequence=["#667eea"])
                fig.update_layout(
                    height=300,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#ccc"),
                    xaxis_title="Rating",
                    yaxis_title="Count",
                )
                st.plotly_chart(fig, use_container_width=True)

            st.dataframe(fb_df, use_container_width=True, hide_index=True)
        else:
            st.info("No feedback recorded yet.")

# ── Sidebar ────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔍 XAI Controls")
    st.markdown("""
    **Explainability Features:**
    - 📄 Source citations (PDF + page)
    - 🗃️ SQL query visibility
    - 🔗 Full agent decision trace
    - 📊 User feedback analytics
    - 🔒 Security audit log
    """)

    st.markdown("---")
    st.markdown("#### 🔗 Quick Links")
    api_url = os.getenv("BACKEND_API_URL", "http://localhost:8000")
    st.markdown(f"- [API Docs]({api_url}/docs)")
    st.markdown(f"- [Health Check]({api_url}/health)")

    langsmith_url = os.getenv("LANGSMITH_URL", "https://smith.langchain.com")
    st.markdown(f"- [LangSmith Traces]({langsmith_url})")
