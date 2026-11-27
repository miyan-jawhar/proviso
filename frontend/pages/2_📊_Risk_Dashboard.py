"""
📊 Risk Dashboard
==================
Compliance metrics, vendor risk heatmap, GDPR gap analysis.
"""

import os
import json
from pathlib import Path
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import requests

st.set_page_config(page_title="Risk Dashboard — Proviso", page_icon="📊", layout="wide")

API_URL = os.getenv("BACKEND_API_URL", "http://localhost:8000")
FRONTEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = (
    FRONTEND_ROOT.parent
    if (FRONTEND_ROOT.parent / "data").exists()
    else FRONTEND_ROOT
)
DATA_DIR = PROJECT_ROOT / "data"
METADATA_FILE = DATA_DIR / "contracts_metadata.json"

# ── Header ─────────────────────────────────────────────────────────
st.markdown("""
<style>
    .risk-header {
        background: linear-gradient(135deg, #f12711 0%, #f5af19 100%);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
    }
    .risk-header h2 { color: white; margin: 0; }
    .risk-header p { color: rgba(255,255,255,0.85); margin: 0.3rem 0 0 0; }
    .metric-card {
        background: linear-gradient(145deg, #1a1a2e, #16213e);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px;
        padding: 1.2rem;
        text-align: center;
    }
    .metric-val { font-size: 2rem; font-weight: 700; color: #667eea; }
    .metric-lbl { font-size: 0.8rem; color: #8892b0; text-transform: uppercase; letter-spacing: 1px; }
</style>
<div class="risk-header">
    <h2>📊 Risk & Compliance Dashboard</h2>
    <p>Real-time vendor compliance monitoring and risk assessment</p>
</div>
""", unsafe_allow_html=True)


def load_data():
    """Load contract metadata — try API first, fall back to local file."""
    try:
        resp = requests.get(f"{API_URL}/analytics/vendors", timeout=5)
        if resp.status_code == 200:
            return pd.DataFrame(resp.json())
    except Exception:
        pass

    # Fallback: load from local file
    if METADATA_FILE.exists():
        with open(METADATA_FILE) as f:
            data = json.load(f)
        return pd.DataFrame(data)

    return pd.DataFrame()


df = load_data()

if df.empty:
    st.warning("No contract data found. Run the data generation scripts first.")
    st.code("python scripts/generate_contracts.py\npython scripts/generate_invoices.py")
    st.stop()

# ── KPI Row ────────────────────────────────────────────────────────
total = len(df)
gdpr_missing = len(df[df.get("include_gdpr", df.get("has_gdpr", True)) == False]) if "include_gdpr" in df.columns or "has_gdpr" in df.columns else 0

# Handle both column names
gdpr_col = "include_gdpr" if "include_gdpr" in df.columns else "has_gdpr"
if gdpr_col in df.columns:
    gdpr_missing = len(df[df[gdpr_col] == False])
    gdpr_ok = total - gdpr_missing
else:
    gdpr_ok = total
    gdpr_missing = 0

fee_col = "late_fee_pct" if "late_fee_pct" in df.columns else None
high_penalty = len(df[df[fee_col] > 10]) if fee_col else 0
compliance_rate = round(gdpr_ok / total * 100, 1) if total else 0

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric("Total Contracts", total)
with c2:
    st.metric("GDPR Compliant", f"{gdpr_ok}/{total}", delta=f"{compliance_rate}%")
with c3:
    st.metric("GDPR Missing", gdpr_missing, delta=f"-{gdpr_missing}" if gdpr_missing else "0", delta_color="inverse")
with c4:
    st.metric("High Penalty Flags", high_penalty, delta=f"⚠️ {high_penalty} contracts" if high_penalty else "Clean")

st.markdown("---")

# ── Risk Heatmap (Treemap) ────────────────────────────────────────
st.markdown("### 🗺️ Vendor Risk Heatmap")

risk_col = "risk_tier" if "risk_tier" in df.columns else None
if risk_col:
    color_map = {"LOW": "#2ed573", "MEDIUM": "#ffa502", "HIGH": "#ff6b35", "CRITICAL": "#ff4757"}

    fig_tree = px.treemap(
        df,
        path=["country", "vendor_name"] if "country" in df.columns else ["vendor_name"],
        values=[1] * len(df),
        color=risk_col,
        color_discrete_map=color_map,
        title="Vendor Risk Distribution by Country",
    )
    fig_tree.update_layout(
        height=500,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#ccc"),
        margin=dict(t=40, l=10, r=10, b=10),
    )
    st.plotly_chart(fig_tree, use_container_width=True)
else:
    st.info("Risk tier data not available.")

# ── Two-Column Analysis ───────────────────────────────────────────
col_left, col_right = st.columns(2)

with col_left:
    # Risk tier distribution
    st.markdown("### 📊 Risk Tier Distribution")
    if risk_col:
        risk_counts = df[risk_col].value_counts()
        fig_pie = go.Figure(data=[go.Pie(
            labels=risk_counts.index,
            values=risk_counts.values,
            marker=dict(colors=[color_map.get(r, "#666") for r in risk_counts.index]),
            hole=0.4,
            textinfo="label+percent",
        )])
        fig_pie.update_layout(
            height=350,
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#ccc"),
            showlegend=False,
            margin=dict(t=10, l=10, r=10, b=10),
        )
        st.plotly_chart(fig_pie, use_container_width=True)

with col_right:
    # Country distribution
    st.markdown("### 🌍 Vendors by Country")
    if "country" in df.columns:
        country_counts = df["country"].value_counts().head(10)
        fig_bar = px.bar(
            x=country_counts.values,
            y=country_counts.index,
            orientation="h",
            color=country_counts.values,
            color_continuous_scale="Viridis",
        )
        fig_bar.update_layout(
            height=350,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#ccc"),
            showlegend=False,
            xaxis_title="Number of Vendors",
            yaxis_title="",
            coloraxis_showscale=False,
            margin=dict(t=10, l=10, r=10, b=40),
        )
        st.plotly_chart(fig_bar, use_container_width=True)

st.markdown("---")

# ── Compliance Gap Table ──────────────────────────────────────────
st.markdown("### 🚨 Compliance Alerts")

alerts = []
for _, row in df.iterrows():
    issues = []
    if gdpr_col in df.columns and row.get(gdpr_col) == False:
        issues.append("❌ Missing GDPR Clause")
    if fee_col and row.get(fee_col, 0) > 10:
        issues.append(f"⚠️ High Penalty: {row[fee_col]}%")

    if issues:
        alerts.append({
            "Vendor": row.get("vendor_name", "?"),
            "Country": row.get("country", "?"),
            "Risk Tier": row.get(risk_col, "?") if risk_col else "?",
            "Issues": " | ".join(issues),
            "Contract": row.get("contract_number", "?"),
        })

if alerts:
    alert_df = pd.DataFrame(alerts)
    st.dataframe(alert_df, use_container_width=True, hide_index=True)
else:
    st.success("✅ No compliance alerts found.")
