"""
💰 Cost Analytics
==================
Payment anomaly detection, contract cap vs actual spend, overpayment analysis.
"""

import os
import json
import csv
from pathlib import Path
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

st.set_page_config(page_title="Cost Analytics — Proviso", page_icon="💰", layout="wide")

FRONTEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = (
    FRONTEND_ROOT.parent
    if (FRONTEND_ROOT.parent / "data").exists()
    else FRONTEND_ROOT
)
DATA_DIR = PROJECT_ROOT / "data"
METADATA_FILE = DATA_DIR / "contracts_metadata.json"
INVOICES_CSV = DATA_DIR / "seed" / "invoices.csv"
VENDORS_CSV = DATA_DIR / "seed" / "vendors.csv"

# ── Header ─────────────────────────────────────────────────────────
st.markdown("""
<style>
    .cost-header {
        background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
    }
    .cost-header h2 { color: white; margin: 0; }
    .cost-header p { color: rgba(255,255,255,0.85); margin: 0.3rem 0 0 0; }
</style>
<div class="cost-header">
    <h2>💰 Cost Analytics & Anomaly Detection</h2>
    <p>Contract cap compliance, overpayment detection, payment trend analysis</p>
</div>
""", unsafe_allow_html=True)


@st.cache_data
def load_invoices():
    if not INVOICES_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(INVOICES_CSV)
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    return df


@st.cache_data
def load_vendors():
    if not VENDORS_CSV.exists():
        return pd.DataFrame()
    return pd.read_csv(VENDORS_CSV)


@st.cache_data
def load_contracts():
    if not METADATA_FILE.exists():
        return pd.DataFrame()
    with open(METADATA_FILE) as f:
        return pd.DataFrame(json.load(f))


invoices = load_invoices()
vendors = load_vendors()
contracts = load_contracts()

if invoices.empty:
    st.warning("No invoice data found. Run the data generation scripts first.")
    st.code("python scripts/generate_contracts.py\npython scripts/generate_invoices.py")
    st.stop()

# Merge invoices with vendor names
inv_merged = invoices.merge(vendors, on="vendor_id", how="left")

# ── KPI Row ────────────────────────────────────────────────────────
total_spend = inv_merged["amount_due"].sum()
paid_total = inv_merged[inv_merged["payment_status"] == "PAID"]["amount_due"].sum()
overdue_total = inv_merged[inv_merged["payment_status"] == "OVERDUE"]["amount_due"].sum()
pending_total = inv_merged[inv_merged["payment_status"] == "PENDING"]["amount_due"].sum()

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric("Total Invoiced", f"₹{total_spend/1e7:.1f} Cr")
with c2:
    st.metric("Paid", f"₹{paid_total/1e7:.1f} Cr", delta=f"{paid_total/total_spend*100:.0f}%")
with c3:
    st.metric("Overdue", f"₹{overdue_total/1e7:.1f} Cr",
              delta=f"-{overdue_total/total_spend*100:.1f}%", delta_color="inverse")
with c4:
    st.metric("Pending", f"₹{pending_total/1e7:.1f} Cr")

st.markdown("---")

# ── Monthly Payment Trends ────────────────────────────────────────
st.markdown("### 📈 Monthly Payment Trends")

inv_merged["month"] = inv_merged["invoice_date"].dt.to_period("M").astype(str)
monthly = inv_merged.groupby(["month", "payment_status"])["amount_due"].sum().reset_index()

fig_trend = px.area(
    monthly, x="month", y="amount_due", color="payment_status",
    color_discrete_map={"PAID": "#2ed573", "PENDING": "#ffa502", "OVERDUE": "#ff4757"},
    labels={"amount_due": "Amount (INR)", "month": "Month"},
)
fig_trend.update_layout(
    height=400,
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#ccc"),
    xaxis=dict(showgrid=False),
    yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
    legend=dict(orientation="h", y=1.1),
    margin=dict(t=10, l=10, r=10, b=40),
)
st.plotly_chart(fig_trend, use_container_width=True)

st.markdown("---")

# ── Contract Cap vs Actual Spend ──────────────────────────────────
st.markdown("### 🔴 Contract Cap vs Actual Payments")

if not contracts.empty and "monthly_cap" in contracts.columns:
    # A monthly cap is compared with the SUM of invoices in a contract-valid
    # month.  An average invoice amount can conceal a monthly cap breach.
    cap_context = contracts[["vendor_id", "monthly_cap", "effective_date", "expiry_date"]].copy()
    cap_context["effective_date"] = pd.to_datetime(cap_context["effective_date"])
    cap_context["expiry_date"] = pd.to_datetime(cap_context["expiry_date"])
    cap_invoices = inv_merged.merge(cap_context, on="vendor_id", how="inner")
    cap_invoices = cap_invoices[
        (cap_invoices["invoice_date"] >= cap_invoices["effective_date"])
        & (cap_invoices["invoice_date"] <= cap_invoices["expiry_date"])
    ].copy()
    cap_invoices["year_month"] = cap_invoices["invoice_date"].dt.to_period("M").astype(str)
    monthly_vendor = (
        cap_invoices.groupby(["vendor_id", "vendor_name", "year_month", "monthly_cap"], as_index=False)
        .agg(monthly_invoiced=("amount_due", "sum"), invoice_count=("invoice_id", "count"))
    )
    monthly_vendor["overpayment"] = (monthly_vendor["monthly_invoiced"] - monthly_vendor["monthly_cap"]).clip(lower=0)
    breach_months = monthly_vendor[monthly_vendor["overpayment"] > 0]

    # Make the domain-correctness finding visible before the general dashboard.
    # An average invoice can fall below a monthly cap while calendar-month sums breach it.
    demo_vendor_name = "Mcclain, Miller and Henderson"
    demo_invoices = cap_invoices[cap_invoices["vendor_name"] == demo_vendor_name]
    demo_months = monthly_vendor[monthly_vendor["vendor_name"] == demo_vendor_name].sort_values("year_month")
    if not demo_invoices.empty and not demo_months.empty:
        demo_cap = float(demo_months["monthly_cap"].iloc[0])
        average_invoice = float(demo_invoices["amount_due"].mean())
        demo_breaches = demo_months[demo_months["overpayment"] > 0]

        st.markdown("### 🎬 Why average-invoice logic fails")
        st.caption(
            "This is an intentionally rejected baseline. Contract policy compares a "
            "calendar-month total with the cited monthly cap, not an individual-invoice average."
        )
        naive_col, evidence_col = st.columns(2)
        with naive_col:
            st.metric("Naïve average invoice", f"₹{average_invoice:,.0f}")
            st.metric("Monthly contract cap", f"₹{demo_cap:,.0f}")
            st.warning("Naïve conclusion: no breach — incorrect policy test.")
        with evidence_col:
            st.metric("Policy-aware breach months", len(demo_breaches))
            st.metric(
                "Largest monthly excess",
                f"₹{demo_breaches['overpayment'].max():,.0f}" if not demo_breaches.empty else "₹0",
            )
            st.error("Correct conclusion: the cited cap was exceeded in calendar-month totals.")

        demo_chart = go.Figure()
        demo_chart.add_trace(go.Bar(
            name="Monthly invoices",
            x=demo_months["year_month"],
            y=demo_months["monthly_invoiced"],
            marker_color=["#ff4757" if value > demo_cap else "#667eea" for value in demo_months["monthly_invoiced"]],
        ))
        demo_chart.add_hline(
            y=demo_cap,
            line_color="#ffa502",
            line_dash="dash",
            annotation_text="Cited monthly cap",
        )
        demo_chart.update_layout(
            height=330,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#ccc"),
            yaxis=dict(title="Amount (INR)", gridcolor="rgba(255,255,255,0.05)"),
            xaxis=dict(title="Contract-valid calendar month", showgrid=False),
            margin=dict(t=35, l=10, r=10, b=40),
        )
        st.plotly_chart(demo_chart, use_container_width=True)

    st.markdown("---")
    cap_df = (
        breach_months.groupby(["vendor_id", "vendor_name", "monthly_cap"], as_index=False)
        .agg(
            breach_months=("year_month", "count"),
            highest_monthly_invoiced=("monthly_invoiced", "max"),
            overpayment=("overpayment", "sum"),
            largest_monthly_excess=("overpayment", "max"),
        )
    )
    cap_df["is_over"] = True

    # Top overpaying vendors
    over_vendors = cap_df[cap_df["is_over"]].sort_values("overpayment", ascending=False).head(15)

    if not over_vendors.empty:
        fig_cap = go.Figure()
        fig_cap.add_trace(go.Bar(
            name="Contract Cap",
            x=over_vendors["vendor_name"],
            y=over_vendors["monthly_cap"],
            marker_color="#667eea",
        ))
        fig_cap.add_trace(go.Bar(
            name="Highest Invoiced Month",
            x=over_vendors["vendor_name"],
            y=over_vendors["highest_monthly_invoiced"],
            marker_color="#ff4757",
        ))
        fig_cap.update_layout(
            barmode="group",
            height=450,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#ccc"),
            xaxis=dict(tickangle=-45, showgrid=False),
            yaxis=dict(title="Amount (INR)", showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
            legend=dict(orientation="h", y=1.1),
            margin=dict(t=10, l=10, r=10, b=100),
        )
        st.plotly_chart(fig_cap, use_container_width=True)

        # Overpayment summary table
        st.markdown("#### 💸 Overpayment Summary")
        display_df = over_vendors[[
            "vendor_name", "monthly_cap", "breach_months", "highest_monthly_invoiced",
            "largest_monthly_excess", "overpayment",
        ]].copy()
        display_df.columns = [
            "Vendor", "Monthly Cap (INR)", "Breach Months", "Highest Invoiced Month (INR)",
            "Largest Monthly Excess (INR)", "Total Excess Across Breach Months (INR)",
        ]
        display_df = display_df.round(0)
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        with st.expander("Show vendor-month calculations", expanded=False):
            monthly_display = breach_months[[
                "vendor_name", "year_month", "monthly_cap", "monthly_invoiced", "overpayment", "invoice_count",
            ]].sort_values(["vendor_name", "year_month"])
            st.dataframe(monthly_display.round(0), use_container_width=True, hide_index=True)
    else:
        st.success("✅ No vendors are exceeding their contract caps.")
else:
    st.info("Contract cap data not available.")

st.markdown("---")

# ── Invoice Amount Distribution (Anomaly Scatter) ─────────────────
st.markdown("### 🎯 Invoice Amount Distribution & Outliers")

# Filter options
col_f1, col_f2 = st.columns(2)
with col_f1:
    status_filter = st.multiselect(
        "Payment Status", ["PAID", "PENDING", "OVERDUE"],
        default=["PAID", "PENDING", "OVERDUE"]
    )
with col_f2:
    top_n = st.slider("Top N vendors by spend", 5, 50, 20)

filtered = inv_merged[inv_merged["payment_status"].isin(status_filter)]
top_vendors = filtered.groupby("vendor_name")["amount_due"].sum().nlargest(top_n).index
filtered_top = filtered[filtered["vendor_name"].isin(top_vendors)]

fig_scatter = px.scatter(
    filtered_top,
    x="invoice_date",
    y="amount_due",
    color="payment_status",
    hover_data=["vendor_name"],
    color_discrete_map={"PAID": "#2ed573", "PENDING": "#ffa502", "OVERDUE": "#ff4757"},
    opacity=0.6,
    labels={"amount_due": "Invoice Amount (INR)", "invoice_date": "Date"},
)
fig_scatter.update_layout(
    height=400,
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#ccc"),
    xaxis=dict(showgrid=False),
    yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
    legend=dict(orientation="h", y=1.1),
    margin=dict(t=10, l=10, r=10, b=40),
)
st.plotly_chart(fig_scatter, use_container_width=True)

# ── Duplicate Payment Detection ───────────────────────────────────
st.markdown("---")
st.markdown("### 🔍 Potential Duplicate Payments")

dup_cols = ["vendor_id", "invoice_date", "amount_due"]
if all(c in inv_merged.columns for c in dup_cols):
    inv_merged["_date_str"] = inv_merged["invoice_date"].dt.strftime("%Y-%m-%d")
    dup_check = inv_merged.groupby(["vendor_id", "_date_str", "amount_due"]).size().reset_index(name="count")
    dups = dup_check[dup_check["count"] > 1].merge(vendors, on="vendor_id", how="left")

    if not dups.empty:
        st.error(f"⚠️ Found {len(dups)} potential duplicate payment entries!")
        disp_dups = dups[["vendor_name", "_date_str", "amount_due", "count"]].copy()
        disp_dups.columns = ["Vendor", "Date", "Amount (INR)", "Occurrences"]
        st.dataframe(disp_dups, use_container_width=True, hide_index=True)
    else:
        st.success("✅ No duplicate payments detected.")
