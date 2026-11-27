"""
Risk Synthesizer Agent
=======================
Third-tier agent that runs AFTER both legal and finance agents have
contributed findings. Produces a structured risk scorecard from their
combined evidence without requiring an additional LLM call for
deterministic cases.

Architecture: legal_agent ──► risk_synthesizer ◄── finance_agent
              risk_synthesizer ──► supervisor (FINISH)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.agents.state import AgentState

logger = logging.getLogger("proviso.risk_synthesizer")

# Severity thresholds (fraction of monthly cap)
_BREACH_SEVERE_THRESHOLD = 0.20    # > 20% over cap → CRITICAL
_BREACH_MODERATE_THRESHOLD = 0.05  # 5–20% over cap → HIGH


def create_risk_synthesizer_node(llm=None):
    """
    Create the Risk Synthesizer node.

    Args:
        llm: Optional LLM for open-ended synthesis. If None, uses
             deterministic rule engine only.
    """

    def risk_synthesizer_node(state: AgentState) -> dict:
        """
        Synthesize legal + finance findings into a structured risk scorecard.

        Deterministic path (no LLM call):
          - Cap breach detected in context_data → severity from breach amount
          - Legal found GDPR gap → adds compliance risk dimension
          - Penalty clause present → adds penalty risk dimension

        Fallback:
          - Both agents returned prose but no structured data → LOW/UNKNOWN
        """
        context = state.get("context_data", {})
        messages = state.get("messages", [])
        cited_sources = state.get("cited_sources", [])

        logger.info("Risk Synthesizer: building scorecard from agent context.")

        # ── 1. Gather structured evidence ──────────────────────────────────
        cap_terms = context.get("contract_cap_terms", [])
        cap_breach = context.get("cap_breach_result", {})
        legal_findings = _extract_agent_findings(messages, "legal_agent")
        finance_findings = _extract_agent_findings(messages, "finance_agent")

        # ── 2. Determine risk dimensions ───────────────────────────────────
        dimensions: list[dict[str, Any]] = []

        # Cap compliance dimension
        if cap_breach:
            breach_months = cap_breach.get("breach_months", [])
            max_excess = cap_breach.get("max_excess_amount", 0) or 0
            monthly_cap = cap_breach.get("monthly_cap", 1) or 1
            breach_ratio = max_excess / monthly_cap

            if breach_ratio > _BREACH_SEVERE_THRESHOLD:
                cap_severity = "CRITICAL"
            elif breach_ratio > _BREACH_MODERATE_THRESHOLD:
                cap_severity = "HIGH"
            elif breach_months:
                cap_severity = "MEDIUM"
            else:
                cap_severity = "LOW"

            dimensions.append({
                "dimension": "Cap Compliance",
                "severity": cap_severity,
                "finding": (
                    f"{len(breach_months)} month(s) exceed the contractual cap; "
                    f"largest excess is {max_excess:,.0f} ({breach_ratio:.1%} over cap)."
                ) if breach_months else "No cap breaches detected in contract-valid period.",
                "evidence_type": "deterministic_sql",
                "citations": [s.get("filename") for s in cited_sources if s.get("filename")],
            })

        # GDPR / data-protection dimension
        gdpr_risk = _detect_gdpr_risk(legal_findings)
        if gdpr_risk is not None:
            dimensions.append(gdpr_risk)

        # Penalty clause dimension
        penalty_risk = _detect_penalty_risk(legal_findings)
        if penalty_risk is not None:
            dimensions.append(penalty_risk)

        # ── 3. Overall severity (worst dimension wins) ─────────────────────
        severity_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}
        if dimensions:
            overall_severity = max(
                dimensions, key=lambda d: severity_order.get(d["severity"], 0)
            )["severity"]
        elif legal_findings or finance_findings:
            overall_severity = "LOW"
        else:
            overall_severity = "UNKNOWN"

        # ── 4. Recommended actions ─────────────────────────────────────────
        actions = _build_recommended_actions(dimensions, cap_breach, cap_terms)

        # ── 5. Assemble scorecard ──────────────────────────────────────────
        scorecard = {
            "overall_severity": overall_severity,
            "risk_dimensions": dimensions,
            "recommended_actions": actions,
            "evidence_summary": {
                "legal_findings_present": bool(legal_findings),
                "finance_findings_present": bool(finance_findings),
                "contract_cap_terms_found": len(cap_terms),
                "breach_months_detected": (
                    len(cap_breach.get("breach_months", [])) if cap_breach else 0
                ),
                "citations_count": len(cited_sources),
            },
            "synthesis_method": (
                "deterministic" if (cap_breach or cap_terms) else "prose_analysis"
            ),
        }

        updated_context = dict(context)
        updated_context["risk_scorecard"] = scorecard

        logger.info(
            "Risk Synthesizer: overall_severity=%s, dimensions=%d, method=%s",
            overall_severity,
            len(dimensions),
            scorecard["synthesis_method"],
        )

        return {
            "messages": [{
                "role": "risk_synthesizer",
                "content": (
                    f"Risk scorecard compiled. Overall severity: {overall_severity}. "
                    f"Dimensions assessed: {len(dimensions)}. "
                    f"Method: {scorecard['synthesis_method']}."
                ),
            }],
            "context_data": updated_context,
            "cited_sources": [],
            "sql_queries": [],
            "error_log": [],
            "review_escalations": [],
            "next_node": "supervisor",
            "final_report": "",
            "iteration_count": state.get("iteration_count", 0),
        }

    return risk_synthesizer_node


# ── Private helpers ────────────────────────────────────────────────────────────

def _extract_agent_findings(messages: list[dict], agent_role: str) -> str:
    """Collect all messages from a specific agent into a single string."""
    parts = [
        m.get("content", "")
        for m in messages
        if m.get("role") == agent_role and m.get("content")
    ]
    return "\n".join(parts)


def _detect_gdpr_risk(legal_text: str) -> dict | None:
    """Return a GDPR risk dimension if legal findings mention compliance gaps."""
    if not legal_text:
        return None
    lower = legal_text.lower()
    if (
        "gdpr" not in lower
        and "data protection" not in lower
        and "data-protection" not in lower
    ):
        return None

    if any(phrase in lower for phrase in (
        "no gdpr", "missing gdpr", "absent", "not found",
        "risk_level: high", "risk_level: critical",
    )):
        return {
            "dimension": "GDPR / Data Protection",
            "severity": "HIGH",
            "finding": "Legal agent detected absent or insufficient data-protection clauses.",
            "evidence_type": "rag_legal",
            "citations": [],
        }
    if any(phrase in lower for phrase in (
        "gdpr compliant", "data protection clause present", "risk_level: low",
    )):
        return {
            "dimension": "GDPR / Data Protection",
            "severity": "LOW",
            "finding": "Data-protection clause present and adequate.",
            "evidence_type": "rag_legal",
            "citations": [],
        }
    return {
        "dimension": "GDPR / Data Protection",
        "severity": "MEDIUM",
        "finding": "Data-protection terms referenced but completeness not fully confirmed.",
        "evidence_type": "rag_legal",
        "citations": [],
    }


def _detect_penalty_risk(legal_text: str) -> dict | None:
    """Return a penalty risk dimension if unusual late-payment fees were found."""
    if not legal_text:
        return None
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:per\s+month|monthly)?\s*(?:late|penalty)",
        legal_text,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"(?:penalty|late[-\s]?(?:payment\s+)?fee)\s+of\s+(\d+(?:\.\d+)?)\s*%",
            legal_text,
            re.IGNORECASE,
        )
    if not match:
        return None
    rate = float(match.group(1))
    if rate > 10:
        return {
            "dimension": "Late-Payment Penalty",
            "severity": "HIGH",
            "finding": (
                f"Late-payment penalty of {rate}% per month is unusually high "
                f"(prototype threshold: 10%)."
            ),
            "evidence_type": "rag_legal",
            "citations": [],
        }
    return {
        "dimension": "Late-Payment Penalty",
        "severity": "LOW",
        "finding": f"Late-payment penalty of {rate}% is within normal range.",
        "evidence_type": "rag_legal",
        "citations": [],
    }


def _build_recommended_actions(
    dimensions: list[dict],
    cap_breach: dict,
    cap_terms: list[dict],
) -> list[str]:
    """Generate prioritised recommended actions from risk dimensions."""
    actions: list[str] = []
    for dim in dimensions:
        severity = dim.get("severity", "")
        dimension = dim.get("dimension", "")
        if severity in ("CRITICAL", "HIGH"):
            if "Cap" in dimension:
                actions.append(
                    "URGENT: Engage vendor for overpayment recovery; "
                    "escalate to Finance and Legal for contract remediation."
                )
            elif "GDPR" in dimension:
                actions.append(
                    "URGENT: Require vendor to provide or execute a Data Processing Agreement "
                    "before the next billing cycle."
                )
            elif "Penalty" in dimension:
                actions.append(
                    "Flag late-payment penalty clause for contract renegotiation; "
                    "industry standard is ≤2% per month."
                )
        elif severity == "MEDIUM":
            if "GDPR" in dimension:
                actions.append(
                    "Request vendor's data protection policy and DPA template within 30 days."
                )
    if not actions:
        actions.append(
            "No immediate action required. "
            "Schedule routine contract review at next renewal."
        )
    return actions
