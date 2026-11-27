from types import SimpleNamespace
import unittest

from app.agents.finance_agent import (
    _build_standard_metric_query,
    _select_query_cap_terms,
    create_finance_node,
)
from app.config import get_settings


class _FinanceLLM:
    def __init__(self, generated_sql: str):
        self.generated_sql = generated_sql

    def invoke(self, messages):
        prompt = "\n".join(str(message.content) for message in messages)
        if "Generate a SELECT query" in prompt:
            return SimpleNamespace(content=f"SQL_QUERY: {self.generated_sql}\nANALYSIS: pending")
        return SimpleNamespace(content="The read-only query completed successfully.")


class _SequenceFinanceLLM:
    def __init__(self, generated_sql: list[str]):
        self.generated_sql = generated_sql
        self.prompts = []

    def invoke(self, messages):
        prompt = "\n".join(str(message.content) for message in messages)
        self.prompts.append(prompt)
        if "Generate a SELECT query" in prompt:
            return SimpleNamespace(content=f"SQL_QUERY: {self.generated_sql.pop(0)}\nANALYSIS: pending")
        return SimpleNamespace(content="The read-only monthly cap query completed successfully.")


def _state(query: str):
    return {
        "messages": [{"role": "user", "content": query}],
        "next_node": "finance_agent",
        "context_data": {},
        "final_report": "",
        "cited_sources": [],
        "sql_queries": [],
        "error_log": [],
        "iteration_count": 0,
    }


class FinanceAgentSafetyTests(unittest.TestCase):
    def test_query_only_sqlite_connection_can_run_an_allowlisted_select(self):
        node = create_finance_node(
            _FinanceLLM("SELECT COUNT(*) AS vendor_count FROM vendors"),
            get_settings().database_readonly_url,
        )

        result = node(_state("How many vendors are there?"))

        self.assertEqual(result["error_log"], [])
        self.assertEqual(result["sql_queries"], ["SELECT COUNT(*) AS vendor_count FROM vendors"])
        self.assertIn("read-only query completed", result["messages"][0]["content"])

    def test_ast_allowlist_rejects_system_catalog_access_before_execution(self):
        node = create_finance_node(
            _FinanceLLM("SELECT name FROM sqlite_master"),
            get_settings().database_readonly_url,
        )

        result = node(_state("List database tables"))

        self.assertIn("SECURITY VIOLATION", result["messages"][0]["content"])
        self.assertIn("outside the allow-list", result["messages"][0]["content"])

    def test_repeated_query_failures_escalate_for_financial_review(self):
        node = create_finance_node(
            _FinanceLLM("SELECT missing_column FROM invoices"),
            get_settings().database_readonly_url,
        )
        state = _state("Show the amount due")
        state["context_data"] = {"legal_findings": "Contract cap found."}

        result = node(state)

        self.assertIn("ABSTAINED", result["messages"][0]["content"])
        self.assertEqual(result["review_escalations"][0]["agent"], "finance_agent")
        self.assertEqual(result["review_escalations"][0]["reason_code"], "finance_query_inconclusive")
        self.assertEqual(result["context_data"]["legal_findings"], "Contract cap found.")

    def test_cap_query_uses_deterministic_monthly_sql_without_an_llm_call(self):
        llm = _SequenceFinanceLLM([])
        node = create_finance_node(llm, get_settings().database_readonly_url)
        state = _state("Have invoices for Mcclain, Miller and Henderson exceeded the contract monthly cap?")
        state["context_data"] = {
            "contract_cap_terms": [{
                "vendor_name": "Mcclain, Miller and Henderson",
                "monthly_cap_inr": 456170,
                "source_file": "contract.pdf",
                "page_number": 2,
                "effective_date": "2024-01-01",
                "expiry_date": "2026-12-31",
            }]
        }

        result = node(state)

        self.assertIn("strftime('%Y-%m', i.invoice_date)", result["sql_queries"][0])
        self.assertIn("SUM(i.amount_due)", result["sql_queries"][0])
        self.assertNotIn("AVG(", result["sql_queries"][0])
        self.assertEqual(result["review_escalations"], [])
        self.assertEqual(llm.prompts, [])
        self.assertIn("BREACH", result["messages"][0]["content"])

    def test_cap_query_without_cited_cap_evidence_escalates_before_model_call(self):
        llm = _SequenceFinanceLLM(["SELECT COUNT(*) FROM invoices"])
        node = create_finance_node(llm, get_settings().database_readonly_url)

        result = node(_state("Are payments over the contract cap?"))

        self.assertEqual(result["review_escalations"][0]["reason_code"], "missing_contract_cap_evidence")
        self.assertEqual(llm.prompts, [])

    def test_multiple_cited_cap_values_escalate_before_sql_or_model_execution(self):
        llm = _SequenceFinanceLLM([])
        node = create_finance_node(llm, get_settings().database_readonly_url)
        state = _state("Is Acme Ltd compliant with its monthly cap in June 2025?")
        state["context_data"] = {
            "contract_cap_terms": [
                {"vendor_name": "Acme Ltd", "monthly_cap_inr": 10_000},
                {"vendor_name": "Acme Ltd", "monthly_cap_inr": 15_000},
            ]
        }

        result = node(state)

        self.assertEqual(result["review_escalations"][0]["reason_code"], "ambiguous_effective_dated_cap_terms")
        self.assertEqual(result["sql_queries"], [])
        self.assertEqual(llm.prompts, [])

    def test_cap_evidence_for_multiple_unmentioned_vendors_is_not_selected(self):
        terms = [
            {"vendor_name": "Vendor A", "monthly_cap_inr": 100},
            {"vendor_name": "Vendor B", "monthly_cap_inr": 200},
        ]

        selected = _select_query_cap_terms(terms, "Is the contract cap compliant?")

        self.assertEqual(selected, [])

    def test_common_average_metric_is_deterministic_without_an_llm_call(self):
        llm = _SequenceFinanceLLM([])
        node = create_finance_node(llm, get_settings().database_readonly_url)

        result = node(_state("What is the average invoice amount for Davis and Sons?"))

        self.assertIn("AVG(i.amount_due)", result["sql_queries"][0])
        self.assertIn("Davis and Sons", result["messages"][0]["content"])
        self.assertEqual(result["review_escalations"], [])
        self.assertEqual(llm.prompts, [])

    def test_standard_metric_template_rejects_unsafe_vendor_literals(self):
        query = "What is the average invoice amount for Davis and Sons; DROP TABLE invoices?"

        self.assertIsNone(_build_standard_metric_query(query, "sqlite"))
