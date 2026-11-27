"""Unit tests for the Risk Synthesizer agent."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from app.agents.risk_synthesizer import (
    create_risk_synthesizer_node,
    _detect_gdpr_risk,
    _detect_penalty_risk,
    _extract_agent_findings,
)


def _make_state(messages=None, context=None, cited_sources=None):
    return {
        "messages": messages or [],
        "context_data": context or {},
        "cited_sources": cited_sources or [],
        "sql_queries": [],
        "error_log": [],
        "review_escalations": [],
        "next_node": "",
        "final_report": "",
        "iteration_count": 2,
        "risk_scorecard": {},
    }


class TestHelpers(unittest.TestCase):

    # ── GDPR detection ────────────────────────────────────────────────────────

    def test_gdpr_missing_returns_high(self):
        result = _detect_gdpr_risk("FINDINGS: no gdpr clause found. RISK_LEVEL: HIGH")
        self.assertIsNotNone(result)
        self.assertEqual(result["severity"], "HIGH")

    def test_gdpr_compliant_returns_low(self):
        result = _detect_gdpr_risk("FINDINGS: gdpr compliant. RISK_LEVEL: LOW")
        self.assertIsNotNone(result)
        self.assertEqual(result["severity"], "LOW")

    def test_no_gdpr_text_returns_none(self):
        self.assertIsNone(_detect_gdpr_risk("Invoice total is 5000."))

    def test_gdpr_ambiguous_returns_medium(self):
        result = _detect_gdpr_risk("data protection is mentioned but not fully assessed.")
        self.assertIsNotNone(result)
        self.assertEqual(result["severity"], "MEDIUM")

    # ── Penalty detection ─────────────────────────────────────────────────────

    def test_high_penalty_detected(self):
        result = _detect_penalty_risk("late payment penalty of 15% per month applies.")
        self.assertIsNotNone(result)
        self.assertEqual(result["severity"], "HIGH")

    def test_normal_penalty_returns_low(self):
        result = _detect_penalty_risk("late payment fee of 2% per month.")
        self.assertIsNotNone(result)
        self.assertEqual(result["severity"], "LOW")

    def test_no_penalty_text_returns_none(self):
        self.assertIsNone(_detect_penalty_risk("GDPR clause present."))

    # ── Agent finding extraction ───────────────────────────────────────────────

    def test_extract_agent_findings_joins_messages(self):
        messages = [
            {"role": "legal_agent", "content": "Part A."},
            {"role": "finance_agent", "content": "Should not appear."},
            {"role": "legal_agent", "content": "Part B."},
        ]
        result = _extract_agent_findings(messages, "legal_agent")
        self.assertIn("Part A.", result)
        self.assertIn("Part B.", result)
        self.assertNotIn("Should not appear", result)

    def test_extract_agent_findings_empty(self):
        self.assertEqual(_extract_agent_findings([], "legal_agent"), "")


class TestRiskSynthesizerNode(unittest.TestCase):

    def setUp(self):
        self.node = create_risk_synthesizer_node(llm=None)

    def test_node_returns_dict_with_scorecard(self):
        state = _make_state(
            messages=[
                {"role": "legal_agent", "content": "FINDINGS: cap of INR 500000. RISK_LEVEL: LOW"},
                {"role": "finance_agent", "content": "Monthly spend: INR 480000. No breach."},
            ],
            context={
                "cap_breach_result": {
                    "breach_months": [],
                    "max_excess_amount": 0,
                    "monthly_cap": 500000,
                }
            },
        )
        result = self.node(state)
        self.assertIn("context_data", result)
        scorecard = result["context_data"].get("risk_scorecard")
        self.assertIsNotNone(scorecard)
        self.assertIn("overall_severity", scorecard)
        self.assertIn("risk_dimensions", scorecard)
        self.assertIn("recommended_actions", scorecard)

    def test_breach_above_20pct_gives_critical(self):
        # 150000/500000 = 30% → CRITICAL
        state = _make_state(
            messages=[
                {"role": "legal_agent", "content": "FINDINGS: cap of INR 500000."},
                {"role": "finance_agent", "content": "Monthly spend: 650000."},
            ],
            context={
                "cap_breach_result": {
                    "breach_months": ["2024-01", "2024-02"],
                    "max_excess_amount": 150000,
                    "monthly_cap": 500000,
                }
            },
        )
        result = self.node(state)
        scorecard = result["context_data"]["risk_scorecard"]
        self.assertEqual(scorecard["overall_severity"], "CRITICAL")

    def test_no_breach_gives_low_or_low_cap_dimension(self):
        state = _make_state(
            messages=[
                {"role": "legal_agent", "content": "FINDINGS: cap. RISK_LEVEL: LOW"},
                {"role": "finance_agent", "content": "No excess."},
            ],
            context={
                "cap_breach_result": {
                    "breach_months": [],
                    "max_excess_amount": 0,
                    "monthly_cap": 500000,
                }
            },
        )
        result = self.node(state)
        scorecard = result["context_data"]["risk_scorecard"]
        cap_dim = next(
            (d for d in scorecard["risk_dimensions"] if "Cap" in d.get("dimension", "")),
            None,
        )
        self.assertIsNotNone(cap_dim)
        self.assertEqual(cap_dim["severity"], "LOW")

    def test_next_node_is_supervisor(self):
        state = _make_state()
        result = self.node(state)
        self.assertEqual(result["next_node"], "supervisor")

    def test_empty_state_gives_unknown_severity(self):
        state = _make_state()
        result = self.node(state)
        scorecard = result["context_data"]["risk_scorecard"]
        self.assertEqual(scorecard["overall_severity"], "UNKNOWN")

    def test_synthesis_method_deterministic_when_breach_present(self):
        state = _make_state(
            messages=[
                {"role": "legal_agent", "content": "cap found"},
                {"role": "finance_agent", "content": "breach found"},
            ],
            context={
                "cap_breach_result": {
                    "breach_months": ["2024-01"],
                    "max_excess_amount": 10000,
                    "monthly_cap": 100000,
                },
                "contract_cap_terms": [{"vendor_name": "X", "monthly_cap_inr": 100000}],
            },
        )
        result = self.node(state)
        scorecard = result["context_data"]["risk_scorecard"]
        self.assertEqual(scorecard["synthesis_method"], "deterministic")

    def test_gdpr_missing_propagates_to_high_severity(self):
        state = _make_state(
            messages=[
                {"role": "legal_agent", "content": "no gdpr clause found. RISK_LEVEL: HIGH"},
                {"role": "finance_agent", "content": "spend within limits."},
            ],
        )
        result = self.node(state)
        scorecard = result["context_data"]["risk_scorecard"]
        self.assertEqual(scorecard["overall_severity"], "HIGH")

    def test_recommended_actions_non_empty(self):
        state = _make_state()
        result = self.node(state)
        actions = result["context_data"]["risk_scorecard"].get("recommended_actions", [])
        self.assertGreater(len(actions), 0)


if __name__ == "__main__":
    unittest.main()
