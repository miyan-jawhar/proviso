"""
Proviso — Terms. Spend. Proof.
Streamlit Frontend — Main Entry Point
=======================================
Multi-page app with chat, risk dashboard, cost analytics, and XAI explorer.
"""

import streamlit as st

st.set_page_config(
    page_title="Proviso — Terms. Spend. Proof.",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* Dark gradient header */
    .main-header {
        background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
        padding: 2rem 2.5rem;
        border-radius: 16px;
        margin-bottom: 2rem;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
    }
    .main-header h1 {
        color: #ffffff;
        font-size: 2.2rem;
        font-weight: 700;
        margin: 0;
        letter-spacing: -0.5px;
    }
    .main-header p {
        color: #a8a4ce;
        font-size: 1rem;
        margin: 0.5rem 0 0 0;
        font-weight: 300;
    }

    /* KPI Cards */
    .kpi-card {
        background: linear-gradient(145deg, #1a1a2e, #16213e);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
        box-shadow: 0 4px 20px rgba(0,0,0,0.15);
        transition: transform 0.2s, box-shadow 0.2s;
    }
    .kpi-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 30px rgba(0,0,0,0.25);
    }
    .kpi-value {
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(135deg, #667eea, #764ba2);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 0;
    }
    .kpi-label {
        color: #8892b0;
        font-size: 0.85rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-top: 0.3rem;
    }

    /* Status badges */
    .badge-critical { background: #ff4757; color: white; padding: 4px 12px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
    .badge-high { background: #ff6b35; color: white; padding: 4px 12px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
    .badge-medium { background: #ffa502; color: #1a1a2e; padding: 4px 12px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
    .badge-low { background: #2ed573; color: #1a1a2e; padding: 4px 12px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }

    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f0c29, #1a1a2e);
    }

    /* Chat messages */
    .stChatMessage {
        border-radius: 12px !important;
    }

    /* Hide default Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}

    /* Smooth animations */
    .element-container {
        transition: opacity 0.3s ease-in;
    }
</style>
""", unsafe_allow_html=True)

# ── Main Page ──────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <h1>⚖️ Proviso</h1>
    <p><strong>Terms. Spend. Proof.</strong> — Evidence-gated contract-to-spend audit</p>
</div>
""", unsafe_allow_html=True)

# Quick overview
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown("""
    <div class="kpi-card">
        <div class="kpi-value">50</div>
        <div class="kpi-label">Vendor Contracts</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown("""
    <div class="kpi-card">
        <div class="kpi-value">10K+</div>
        <div class="kpi-label">Invoice Records</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown("""
    <div class="kpi-card">
        <div class="kpi-value">2</div>
        <div class="kpi-label">AI Agents</div>
    </div>
    """, unsafe_allow_html=True)

with col4:
    st.markdown("""
    <div class="kpi-card">
        <div class="kpi-value">24/7</div>
        <div class="kpi-label">Monitoring</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")

# Navigation guide
st.markdown("### 🧭 Navigation")
c1, c2 = st.columns(2)
with c1:
    st.info("💬 **Chat** — Query the AI agents in natural language")
    st.info("📊 **Risk Dashboard** — Compliance heatmap & risk overview")
with c2:
    st.info("💰 **Cost Analytics** — Payment anomalies & overspend detection")
    st.info("🔍 **XAI Explorer** — Full explainability: sources, SQL, agent traces")

st.markdown("---")
st.markdown("### 🏗️ Architecture")
st.markdown("""
```
User Query → FastAPI Gateway (JWT Auth) → LangGraph Supervisor
    ├── Legal Agent (RAG) → ChromaDB (50 Vendor Contracts)
    └── Finance Agent (SQL) → PostgreSQL (10K+ Invoices)
        → Synthesized Report with Citations + SQL Queries (XAI)
```
""")

# Sidebar auth
with st.sidebar:
    st.markdown("### 🔐 Authentication")
    if "auth_token" not in st.session_state:
        st.session_state.auth_token = None
        st.session_state.username = None

    if st.session_state.auth_token:
        st.success(f"Logged in as **{st.session_state.username}**")
        if st.button("Logout", use_container_width=True):
            st.session_state.auth_token = None
            st.session_state.username = None
            st.rerun()
    else:
        st.warning("Not authenticated")
        with st.form("login_form"):
            username = st.text_input("Username", value="admin")
            password = st.text_input("Password", type="password", value="admin123")
            submit = st.form_submit_button("Login", use_container_width=True)

            if submit:
                import requests, os
                api_url = os.getenv("BACKEND_API_URL", "http://localhost:8000")
                try:
                    resp = requests.post(
                        f"{api_url}/auth/login",
                        json={"username": username, "password": password},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        st.session_state.auth_token = data["access_token"]
                        st.session_state.username = username
                        st.rerun()
                    else:
                        st.error("Invalid credentials")
                except requests.exceptions.ConnectionError:
                    # Fallback: allow login without backend for demo
                    if username == "admin" and password == "admin123":
                        st.session_state.auth_token = "demo-token"
                        st.session_state.username = username
                        st.rerun()
                    else:
                        st.error("Backend unavailable & credentials invalid")

    st.markdown("---")
    st.caption("Proviso v1.0 · Terms. Spend. Proof.")
    st.caption("Powered by LangGraph + FastAPI + ChromaDB")
