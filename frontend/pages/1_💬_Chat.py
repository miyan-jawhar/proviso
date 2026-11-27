"""
💬 Chat Interface
==================
Natural language querying of the multi-agent system.
Shows AI responses with expandable sources, SQL queries, and agent traces.
"""

import os
import json
import streamlit as st
import requests

st.set_page_config(page_title="Chat — Proviso", page_icon="💬", layout="wide")

API_URL = os.getenv("BACKEND_API_URL", "http://localhost:8000")


def submit_feedback(query_text: str, response_text: str, rating: int) -> bool:
    """Persist a feedback signal through the authenticated Proviso API."""
    token = st.session_state.get("auth_token")
    if not token:
        st.warning("Log in before submitting feedback.")
        return False
    try:
        response = requests.post(
            f"{API_URL}/feedback",
            json={
                "query_text": query_text or "Conversation context unavailable",
                "response_text": response_text,
                "rating": rating,
                "comment": None,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status_code == 200:
            return True
        st.error(f"Feedback could not be saved ({response.status_code}).")
    except requests.RequestException:
        st.error("Feedback service is unavailable.")
    return False

# ── Session State Init ─────────────────────────────────────────────
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "auth_token" not in st.session_state:
    st.session_state.auth_token = None

# ── Header ─────────────────────────────────────────────────────────
st.markdown("""
<style>
    .chat-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
    }
    .chat-header h2 { color: white; margin: 0; }
    .chat-header p { color: rgba(255,255,255,0.8); margin: 0.3rem 0 0 0; font-size: 0.9rem; }
    .source-box {
        background: #1a1a2e;
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 8px;
        padding: 0.8rem;
        margin: 0.3rem 0;
        font-size: 0.85rem;
    }
    .sql-box {
        background: #0d1117;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 1rem;
        font-family: 'Consolas', monospace;
        font-size: 0.85rem;
    }
</style>
<div class="chat-header">
    <h2>💬 AI Agent Chat</h2>
    <p>Ask about vendor contracts, compliance, payments, or cost anomalies</p>
</div>
""", unsafe_allow_html=True)

# ── 90-second demo path ────────────────────────────────────────────
st.markdown("### 🎬 Evidence-gated demo")
st.caption(
    "Start with the deterministic monthly-cap check, then show the system refusing "
    "an ambiguous alias or an unindexed amendment."
)
demo_scenarios = [
    (
        "1. Prove monthly breaches",
        "Are we overpaying Mcclain Miller and Henderson based on their contract terms?",
    ),
    (
        "2. Refuse ambiguous alias",
        "Did M. M. Henderson breach the monthly cap?",
    ),
    (
        "3. Refuse unverified amendment",
        "An email says Mcclain, Miller and Henderson's monthly cap was amended to INR 2,000,000. Is its May 2025 invoice spend compliant?",
    ),
]
demo_cols = st.columns(3)
for index, (label, query_text) in enumerate(demo_scenarios):
    with demo_cols[index]:
        if st.button(label, key=f"demo_{index}", use_container_width=True):
            st.session_state.pending_query = query_text

# ── Example Queries ────────────────────────────────────────────────
with st.expander("📝 Example Queries", expanded=False):
    examples = [
        "Which vendors are missing GDPR compliance clauses?",
        "How much did we pay to the top 5 vendors last year?",
        "Are we overpaying any vendors based on their contract terms?",
        "Show all contracts with penalty fees above 10%",
        "List all overdue invoices exceeding INR 5,00,000",
        "What is the termination clause for vendor with ID 3?",
    ]
    cols = st.columns(2)
    for i, ex in enumerate(examples):
        with cols[i % 2]:
            if st.button(ex, key=f"ex_{i}", use_container_width=True):
                st.session_state.pending_query = ex

# ── Chat History Display ───────────────────────────────────────────
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"], avatar="🧑‍💼" if msg["role"] == "user" else "🤖"):
        st.markdown(msg["content"])

        # Show expandable metadata for AI responses
        if msg["role"] == "assistant" and "metadata" in msg:
            meta = msg["metadata"]

            # Evidence-gated review handoffs are deliberately shown before
            # normal traces: they are an outcome, not an incidental warning.
            if meta.get("review_escalations"):
                for escalation in meta["review_escalations"]:
                    st.warning(
                        f"Human review required — {escalation.get('reason', 'Insufficient reliable evidence.')}",
                        icon="⚠️",
                    )
                    st.caption(
                        f"Recommended action: {escalation.get('recommended_action', 'Provide authoritative evidence.')}"
                    )

            # Sources
            if meta.get("cited_sources"):
                with st.expander(f"📄 Sources ({len(meta['cited_sources'])} documents)", expanded=False):
                    for src in meta["cited_sources"]:
                        score = src.get("relevance_score")
                        confidence = (
                            f" | Retrieval score: {score:.2f}"
                            if isinstance(score, (float, int))
                            else ""
                        )
                        st.markdown(
                            f"**{src.get('filename', '?')}** — Page {src.get('page_number', '?')} "
                            f"| Vendor: {src.get('vendor_name', '?')}{confidence}"
                        )
                        if src.get("clause"):
                            st.caption(f"_{src['clause'][:200]}_")
                        st.markdown("---")

            # SQL Queries
            if meta.get("sql_queries"):
                with st.expander(f"🗃️ SQL Queries ({len(meta['sql_queries'])} executed)", expanded=False):
                    for q in meta["sql_queries"]:
                        st.code(q, language="sql")

            # Agent Trace
            if meta.get("agent_trace"):
                with st.expander("🔗 Agent Trace", expanded=False):
                    for step in meta["agent_trace"]:
                        agent = step.get("agent", "?")
                        action = step.get("action", "")
                        icon = "⚖️" if "legal" in agent else "💰" if "finance" in agent else "🎯"
                        st.markdown(f"{icon} **{agent}**: {action[:200]}")

            # Feedback
            fcol1, fcol2, _ = st.columns([1, 1, 6])
            msg_key = f"fb_{hash(msg['content'][:50])}"
            with fcol1:
                if st.button("👍", key=f"{msg_key}_up"):
                    if submit_feedback(msg.get("query_text", ""), msg["content"], 5):
                        st.toast("Thanks for the feedback!", icon="✅")
            with fcol2:
                if st.button("👎", key=f"{msg_key}_down"):
                    if submit_feedback(msg.get("query_text", ""), msg["content"], 1):
                        st.toast("Feedback noted. We'll improve!", icon="📝")

# ── Chat Input ─────────────────────────────────────────────────────
pending = st.session_state.pop("pending_query", None)
user_input = st.chat_input("Ask about contracts, compliance, or payments...")

query = pending or user_input

if query:
    # Add user message
    st.session_state.chat_history.append({"role": "user", "content": query})
    with st.chat_message("user", avatar="🧑‍💼"):
        st.markdown(query)

    # Call backend
    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("🔍 Agents are analyzing your query..."):
            try:
                headers = {}
                if st.session_state.auth_token:
                    headers["Authorization"] = f"Bearer {st.session_state.auth_token}"

                resp = requests.post(
                    f"{API_URL}/query",
                    json={"query": query},
                    headers=headers,
                    timeout=120,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    answer = data.get("answer", "No response generated.")
                    st.markdown(answer)

                    metadata = {
                        "cited_sources": data.get("cited_sources", []),
                        "sql_queries": data.get("sql_queries", []),
                        "agent_trace": data.get("agent_trace", []),
                        "iterations": data.get("iterations", 0),
                        "review_escalations": data.get("review_escalations", []),
                    }

                    if metadata["review_escalations"]:
                        for escalation in metadata["review_escalations"]:
                            st.warning(
                                f"Human review required — {escalation.get('reason', 'Insufficient reliable evidence.')}",
                                icon="⚠️",
                            )
                            st.caption(
                                f"Recommended action: {escalation.get('recommended_action', 'Provide authoritative evidence.')}"
                            )

                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": answer,
                        "query_text": query,
                        "metadata": metadata,
                    })
                else:
                    error_msg = f"⚠️ Backend returned {resp.status_code}: {resp.text}"
                    st.error(error_msg)
                    st.session_state.chat_history.append({
                        "role": "assistant", "content": error_msg
                    })

            except requests.exceptions.ConnectionError:
                fallback = (
                    "⚠️ **Backend is not running.** Start it with:\n\n"
                    "```bash\ncd backend && uvicorn app.main:app --reload\n```\n\n"
                    "Or use Docker Compose:\n"
                    "```bash\ndocker compose up\n```\n\n"
                    f"Your query was: *{query}*"
                )
                st.warning(fallback)
                st.session_state.chat_history.append({
                    "role": "assistant", "content": fallback
                })

# ── Sidebar ────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 💬 Chat Controls")
    if st.button("🗑️ Clear History", use_container_width=True):
        st.session_state.chat_history = []
        st.rerun()

    st.markdown("---")
    st.markdown(f"**Messages:** {len(st.session_state.chat_history)}")
    st.markdown(f"**Backend:** `{API_URL}`")

    if st.session_state.auth_token:
        st.success(f"🔐 Authenticated")
    else:
        st.warning("🔓 Not authenticated — go to main page to login")
