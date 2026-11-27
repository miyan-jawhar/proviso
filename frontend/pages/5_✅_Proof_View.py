"""A focused, deterministic demonstration of Proviso's cap-compliance policy."""

from __future__ import annotations

import json
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st


st.set_page_config(page_title="Proof view — Proviso", page_icon=":material/verified:", layout="wide")

FRONTEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = FRONTEND_ROOT.parent if (FRONTEND_ROOT.parent / "data").exists() else FRONTEND_ROOT
DATA_DIR = PROJECT_ROOT / "data"
METADATA_FILE = DATA_DIR / "contracts_metadata.json"
INVOICES_CSV = DATA_DIR / "seed" / "invoices.csv"
VENDORS_CSV = DATA_DIR / "seed" / "vendors.csv"


@st.cache_data
def load_proof_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load the local synthetic fixture once, then filter it interactively."""
    if not all(path.exists() for path in (METADATA_FILE, INVOICES_CSV, VENDORS_CSV)):
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    with METADATA_FILE.open(encoding="utf-8") as handle:
        contracts = pd.DataFrame(json.load(handle))
    invoices = pd.read_csv(INVOICES_CSV)
    invoices["invoice_date"] = pd.to_datetime(invoices["invoice_date"])
    vendors = pd.read_csv(VENDORS_CSV)
    return contracts, invoices, vendors


contracts, invoices, vendors = load_proof_data()
if contracts.empty or invoices.empty or vendors.empty:
    st.error("Local proof data is missing. Generate contracts and invoices before opening this view.")
    st.code("python scripts/generate_contracts.py\npython scripts/generate_invoices.py")
    st.stop()

st.title("Proof view")
st.caption("Same ledger. Same vendor. One flawed comparison versus one contract-valid decision.")

default_vendor = "Mcclain, Miller and Henderson"
vendor_names = contracts["vendor_name"].sort_values().tolist()
default_index = vendor_names.index(default_vendor) if default_vendor in vendor_names else 0
selected_vendor = st.selectbox(
    "Vendor",
    vendor_names,
    index=default_index,
    help="The default vendor demonstrates why an invoice average cannot test a monthly contract cap.",
)

contract = contracts.loc[contracts["vendor_name"] == selected_vendor].iloc[0]
vendor_id = int(contract["vendor_id"])
effective_date = pd.Timestamp(contract["effective_date"])
expiry_date = pd.Timestamp(contract["expiry_date"])
monthly_cap = float(contract["monthly_cap"])

vendor_invoices = invoices.loc[invoices["vendor_id"] == vendor_id].copy()
contract_valid = vendor_invoices.loc[
    (vendor_invoices["invoice_date"] >= effective_date)
    & (vendor_invoices["invoice_date"] <= expiry_date)
].copy()
contract_valid["month"] = contract_valid["invoice_date"].dt.strftime("%Y-%m")
monthly = (
    contract_valid.groupby("month", as_index=False)
    .agg(monthly_invoiced=("amount_due", "sum"), invoice_count=("invoice_id", "count"))
    .sort_values("month")
)
monthly["monthly_cap"] = monthly_cap
monthly["excess"] = (monthly["monthly_invoiced"] - monthly_cap).clip(lower=0)
monthly["decision"] = monthly["excess"].gt(0).map({True: "Breach", False: "Within cap"})
breaches = monthly.loc[monthly["decision"] == "Breach"].copy()
average_invoice = float(contract_valid["amount_due"].mean())
naive_says_no_breach = average_invoice <= monthly_cap

left, right = st.columns(2, vertical_alignment="top")
with left:
    with st.container(border=True):
        st.subheader(":material/error: Naïve baseline")
        st.caption("This comparison is intentionally flawed: it compares an invoice average with a monthly policy limit.")
        st.metric("Average contract-valid invoice", f"₹{average_invoice:,.0f}", border=True)
        st.metric("Monthly contract cap", f"₹{monthly_cap:,.0f}", border=True)
        if naive_says_no_breach:
            st.warning("Naïve result: no breach detected.")
        else:
            st.warning("Naïve result: a breach may exist, but this still does not test monthly compliance.")
        if not breaches.empty and naive_says_no_breach:
            st.error("This is wrong for the contract policy: several calendar-month totals exceed the cap.")
        else:
            st.info("An average cannot establish monthly compliance, even when it happens to agree with the result.")

with right:
    with st.container(border=True):
        st.subheader(":material/verified: Proviso decision")
        st.caption("Contract-valid invoices are summed by calendar month and compared with the cited monthly cap.")
        st.metric("Breach months", len(breaches), border=True)
        st.metric(
            "Largest monthly excess",
            f"₹{breaches['excess'].max():,.0f}" if not breaches.empty else "₹0",
            border=True,
        )
        if breaches.empty:
            st.success("No contract-valid calendar month exceeds the cap.")
        else:
            st.error(f"Cap exceeded in {len(breaches)} contract-valid calendar month(s).")
        st.caption("The conclusion is deterministic: no LLM arithmetic or average-invoice heuristic is involved.")

st.space("small")
with st.container(border=True):
    st.subheader("Contract-to-spend proof")
    evidence, computation, outcome = st.columns(3, vertical_alignment="top")
    with evidence:
        st.markdown("**1. Contract evidence**")
        st.write(contract["filename"])
        st.caption(f"{contract['contract_number']} · {effective_date:%d %b %Y} to {expiry_date:%d %b %Y}")
        st.caption(f"Indexed term: maximum monthly billing cap of ₹{monthly_cap:,.0f}.")
    with computation:
        st.markdown("**2. Constrained computation**")
        st.write(f"{len(contract_valid):,} contract-valid invoices")
        st.caption(f"Grouped into {len(monthly)} vendor-month records; dates outside the term are excluded.")
    with outcome:
        st.markdown("**3. Evidence-bounded outcome**")
        st.write("No over-cap month found" if breaches.empty else f"{len(breaches)} months require review")
        st.caption("Page-level retrieval citations and the agent trace are available through the authenticated Chat route.")

chart_data = monthly.copy()
bars = alt.Chart(chart_data).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
    x=alt.X("month:N", title="Contract-valid calendar month", sort=None),
    y=alt.Y("monthly_invoiced:Q", title="Invoice total (INR)"),
    color=alt.Color(
        "decision:N",
        scale=alt.Scale(domain=["Within cap", "Breach"], range=["#4C78A8", "#E45756"]),
        legend=alt.Legend(title="Decision"),
    ),
    tooltip=[
        alt.Tooltip("month:N", title="Month"),
        alt.Tooltip("monthly_invoiced:Q", title="Monthly invoices", format=",.2f"),
        alt.Tooltip("monthly_cap:Q", title="Contract cap", format=",.2f"),
        alt.Tooltip("excess:Q", title="Excess", format=",.2f"),
        alt.Tooltip("invoice_count:Q", title="Invoices"),
    ],
)
cap_rule = alt.Chart(pd.DataFrame({"monthly_cap": [monthly_cap]})).mark_rule(
    color="#F2A900", strokeDash=[6, 4], size=2
).encode(y="monthly_cap:Q")
st.altair_chart(
    (bars + cap_rule).properties(height=360, title="Monthly invoice totals against the cited cap"),
    width="stretch",
)

if not breaches.empty:
    st.subheader("Exact breach months")
    breach_display = breaches[["month", "monthly_invoiced", "monthly_cap", "excess", "invoice_count"]].rename(
        columns={
            "month": "Month",
            "monthly_invoiced": "Invoice total",
            "monthly_cap": "Contract cap",
            "excess": "Variance",
            "invoice_count": "Invoices",
        }
    )
    st.dataframe(
        breach_display,
        column_config={
            "Invoice total": st.column_config.NumberColumn(format="₹%,.2f"),
            "Contract cap": st.column_config.NumberColumn(format="₹%,.2f"),
            "Variance": st.column_config.NumberColumn(format="₹%,.2f"),
            "Invoices": st.column_config.NumberColumn(format="%d"),
        },
        hide_index=True,
        width="stretch",
    )

with st.expander("Show the read-only SQL shape", expanded=False):
    st.caption("Equivalent parameterized SQLite query used for this bounded calculation. The Finance Agent records its executed SQL in Chat responses.")
    st.code(
        """SELECT
    strftime('%Y-%m', i.invoice_date) AS invoice_month,
    SUM(i.amount_due) AS monthly_invoiced,
    ? AS monthly_cap,
    SUM(i.amount_due) - ? AS excess
FROM invoices AS i
WHERE i.vendor_id = ?
  AND i.invoice_date BETWEEN ? AND ?
GROUP BY invoice_month
HAVING SUM(i.amount_due) > ?
ORDER BY invoice_month;""",
        language="sql",
    )
    st.code(
        f"Parameters: monthly_cap={monthly_cap:.2f}, vendor_id={vendor_id}, "
        f"effective_date={effective_date.date()}, expiry_date={expiry_date.date()}",
        language="text",
    )

st.caption("Synthetic demonstration fixture. This view validates the contract-month policy; it is not evidence of real-world savings or independently measured accuracy.")

# ── Live Agent Audit Hub ───────────────────────────────────────────────────────
st.divider()
st.subheader("🤖 Live Agent Audit")
st.caption(
    "Trigger the full 3-agent pipeline for this vendor. "
    "Legal Agent retrieves contract clauses (RAG) → Finance Agent queries the ledger (SQL) "
    "→ Risk Synthesizer compiles a scorecard. Requires authentication."
)

BACKEND_URL = os.environ.get("BACKEND_API_URL", "http://localhost:8000")

# Persistent session keys
if "audit_token" not in st.session_state:
    st.session_state["audit_token"] = None
if "audit_username" not in st.session_state:
    st.session_state["audit_username"] = ""
if "last_audit_result" not in st.session_state:
    st.session_state["last_audit_result"] = None
if "last_audit_query" not in st.session_state:
    st.session_state["last_audit_query"] = ""

# ── Login panel ───────────────────────────────────────────────────────────────
if not st.session_state["audit_token"]:
    with st.expander("🔐 Sign in to run audit", expanded=True):
        col_u, col_p, col_btn = st.columns([2, 2, 1])
        with col_u:
            uname = st.text_input("Username", value="admin", key="audit_uname")
        with col_p:
            pwd = st.text_input("Password", type="password", value="admin123", key="audit_pwd")
        with col_btn:
            st.write("")
            st.write("")
            if st.button("Sign in", key="audit_login_btn"):
                try:
                    import requests as _req
                    resp = _req.post(
                        f"{BACKEND_URL}/auth/login",
                        json={"username": uname, "password": pwd},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        st.session_state["audit_token"] = resp.json().get("access_token")
                        st.session_state["audit_username"] = uname
                        st.success(f"Signed in as {uname}")
                        st.rerun()
                    else:
                        st.error(f"Login failed ({resp.status_code}) — start the backend first.")
                except Exception as exc:
                    st.warning(f"Backend unreachable: {exc} — run `uvicorn app.main:app`")

# ── Audit panel (authenticated) ───────────────────────────────────────────────
if st.session_state["audit_token"]:
    _col_status, _col_signout = st.columns([4, 1])
    with _col_status:
        st.success(f"✓ Signed in as **{st.session_state['audit_username']}**")
    with _col_signout:
        if st.button("Sign out", key="audit_signout"):
            st.session_state["audit_token"] = None
            st.session_state["last_audit_result"] = None
            st.rerun()

    audit_queries = {
        f"Are we overpaying {selected_vendor} based on their contract terms?":
            "Cross-domain: cap audit (Legal → Finance → Risk Synthesizer)",
        f"What is the monthly billing cap for {selected_vendor}?":
            "Legal: cap extraction (RAG)",
        f"Does {selected_vendor} have a GDPR data protection clause?":
            "Legal: GDPR check (RAG)",
        f"Show me invoice totals for {selected_vendor} by month":
            "Finance: spend analysis (SQL)",
    }
    query_label = st.selectbox(
        "Audit query",
        list(audit_queries.keys()),
        format_func=lambda q: f"{audit_queries[q]}",
        key="audit_query_select",
    )
    st.caption(f"Query: *{query_label}*")

    if st.button(
        f"🔍 Run Agent Audit for {selected_vendor}",
        type="primary",
        key="run_audit_btn",
    ):
        import requests as _req
        with st.spinner(
            "Running 3-agent pipeline… Legal Agent → Finance Agent → Risk Synthesizer"
        ):
            try:
                resp = _req.post(
                    f"{BACKEND_URL}/query",
                    json={"query": query_label},
                    headers={"Authorization": f"Bearer {st.session_state['audit_token']}"},
                    timeout=120,
                )
                if resp.status_code == 401:
                    st.session_state["audit_token"] = None
                    st.error("Session expired — please sign in again.")
                    st.rerun()
                elif resp.status_code == 200:
                    st.session_state["last_audit_result"] = resp.json()
                    st.session_state["last_audit_query"] = query_label
                else:
                    st.error(f"Agent error {resp.status_code}: {resp.text[:300]}")
            except Exception as exc:
                st.error(f"Backend unreachable: {exc}")

# ── Audit result display ───────────────────────────────────────────────────────
if st.session_state.get("last_audit_result"):
    result = st.session_state["last_audit_result"]
    query_used = st.session_state.get("last_audit_query", "")

    st.markdown(f"**Query:** *{query_used}*")
    st.markdown("---")

    # Risk Scorecard (shown prominently if available)
    scorecard = result.get("risk_scorecard")
    if scorecard and isinstance(scorecard, dict):
        severity = scorecard.get("overall_severity", "UNKNOWN")
        _sev_icons = {
            "CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢", "UNKNOWN": "⚪",
        }
        icon = _sev_icons.get(severity, "⚪")

        with st.container(border=True):
            st.markdown(f"### {icon} Risk Scorecard — Overall: **{severity}**")
            st.caption(
                f"Method: {scorecard.get('synthesis_method', '?')} · "
                f"Citations: {scorecard.get('evidence_summary', {}).get('citations_count', 0)}"
            )
            dims = scorecard.get("risk_dimensions", [])
            if dims:
                for dim in dims:
                    dim_sev = dim.get("severity", "?")
                    dim_icon = _sev_icons.get(dim_sev, "⚪")
                    dcol1, dcol2 = st.columns([1, 4])
                    with dcol1:
                        st.metric(dim.get("dimension", "Risk"), f"{dim_icon} {dim_sev}")
                    with dcol2:
                        st.write(dim.get("finding", ""))
                        st.caption(f"Evidence: {dim.get('evidence_type', 'N/A')}")
            actions = scorecard.get("recommended_actions", [])
            if actions:
                st.markdown("**Recommended Actions:**")
                for action in actions:
                    st.markdown(f"- {action}")

    # Agent answer
    answer = result.get("answer", "")
    if answer:
        with st.container(border=True):
            st.markdown("### 📋 Agent Response")
            st.markdown(answer)

    # Contract citations
    sources = result.get("cited_sources", [])
    if sources:
        with st.expander(f"📄 Contract Citations ({len(sources)})", expanded=False):
            for src in sources:
                st.markdown(
                    f"**{src.get('filename', 'Unknown')}** "
                    f"p.{src.get('page_number', '?')} — "
                    f"*{src.get('vendor_name', '')}*"
                )
                if src.get("clause"):
                    st.caption(f"> {src['clause'][:200]}")

    # SQL queries
    sql_queries = result.get("sql_queries", [])
    if sql_queries:
        with st.expander(f"🗄️ Executed SQL ({len(sql_queries)})", expanded=False):
            for sql in sql_queries:
                st.code(sql, language="sql")

    # Agent trace
    trace = result.get("agent_trace", [])
    if trace:
        with st.expander(f"🔗 Agent Trace ({len(trace)} steps)", expanded=False):
            for step in trace:
                role = step.get("agent", step.get("role", "?"))
                content = step.get("action", step.get("content", ""))[:300]
                st.markdown(f"**{role}:** {content}")

    # Flags & escalations
    errors = result.get("error_log", [])
    escalations = result.get("review_escalations", [])
    if errors or escalations:
        with st.expander("⚠️ Flags & Escalations", expanded=False):
            for e in errors:
                st.warning(e)
            for esc in escalations:
                st.error(
                    f"**{esc.get('agent', 'agent')}** [{esc.get('reason_code', '')}]: "
                    f"{esc.get('reason', '')}"
                )

st.caption("Synthetic demonstration fixture. This view validates the contract-month policy; it is not evidence of real-world savings or independently measured accuracy.")
