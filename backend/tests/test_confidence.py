import unittest

from langchain_core.documents import Document

from app.agents.legal_agent import (
    _add_vendor_cap_evidence,
    _deterministic_contract_analysis,
    _matched_vendor_name,
    _unmatched_vendor_reference,
    create_legal_node,
)
from app.agents.review import build_review_escalation
from app.agents.supervisor import create_supervisor_node
from app.schemas import QueryResponse


class _NeverCallLLM:
    def invoke(self, _messages):
        raise AssertionError("The LLM must not be called after a low-confidence retrieval.")


class _LowConfidenceVectorStore:
    def similarity_search_with_relevance_scores(self, _query, k):
        self.asserted_k = k
        return [
            (
                Document(
                    page_content="Unrelated contract paragraph.",
                    metadata={"filename": "contract.pdf", "vendor_name": "Unknown"},
                ),
                0.12,
            )
        ]


def _state():
    return {
        "messages": [{"role": "user", "content": "Does Unknown Vendor meet GDPR?"}],
        "next_node": "legal_agent",
        "context_data": {},
        "final_report": "",
        "cited_sources": [],
        "sql_queries": [],
        "error_log": [],
        "iteration_count": 1,
    }


class RetrievalConfidenceTests(unittest.TestCase):
    def test_full_vendor_name_is_safely_bound_to_chroma_metadata(self):
        class _VendorMetadataStore:
            def get(self, include):
                self.include = include
                return {
                    "metadatas": [
                        {"vendor_name": "Mcclain, Miller and Henderson"},
                        {"vendor_name": "Blair PLC"},
                    ]
                }

        store = _VendorMetadataStore()

        matched = _matched_vendor_name(
            store,
            "Are we overpaying Mcclain Miller and Henderson based on contract terms?",
        )

        self.assertEqual(matched, "Mcclain, Miller and Henderson")
        self.assertEqual(store.include, ["metadatas"])

    def test_partial_vendor_collision_is_not_bound(self):
        class _VendorMetadataStore:
            def get(self, include):
                return {
                    "metadatas": [
                        {"vendor_name": "Henderson Ramirez and Lewis"},
                        {"vendor_name": "Mcclain, Miller and Henderson"},
                    ]
                }

        self.assertIsNone(_matched_vendor_name(_VendorMetadataStore(), "Review Henderson invoices"))

    def test_punctuation_and_conjunction_normalization_keeps_one_legal_entity(self):
        class _VendorMetadataStore:
            def get(self, include):
                return {"metadatas": [{"vendor_name": "Mcclain, Miller and Henderson"}]}

        self.assertEqual(
            _matched_vendor_name(
                _VendorMetadataStore(),
                "Review Mcclain, Miller & Henderson for contract-cap compliance.",
            ),
            "Mcclain, Miller and Henderson",
        )

    def test_legal_suffix_and_subsidiary_variants_do_not_bind_to_base_vendor(self):
        class _VendorMetadataStore:
            def get(self, include):
                return {"metadatas": [
                    {"vendor_name": "Doyle Ltd"},
                    {"vendor_name": "Davis and Sons"},
                ]}

        store = _VendorMetadataStore()
        self.assertIsNone(_matched_vendor_name(store, "Review Doyle Limited for GDPR obligations."))
        self.assertIsNone(_matched_vendor_name(store, "Audit Davis and Sons India for its cap."))
        self.assertEqual(
            _unmatched_vendor_reference(store, "Review Doyle Limited for GDPR obligations."),
            "Doyle Limited",
        )

    def test_most_specific_full_vendor_match_beats_a_shorter_overlap(self):
        class _VendorMetadataStore:
            def get(self, include):
                return {
                    "metadatas": [
                        {"vendor_name": "Rodriguez LLC"},
                        {"vendor_name": "Rodriguez, Figueroa and Sanchez"},
                    ]
                }

        self.assertEqual(
            _matched_vendor_name(
                _VendorMetadataStore(),
                "What late-payment penalty applies to Rodriguez, Figueroa and Sanchez?",
            ),
            "Rodriguez, Figueroa and Sanchez",
        )

    def test_unknown_named_vendor_escalates_even_when_semantic_score_is_high(self):
        class _HighSimilarityUnknownVendorStore:
            def get(self, include):
                return {"metadatas": [{"vendor_name": "Acme Ltd"}]}

            def similarity_search_with_relevance_scores(self, _query, **_kwargs):
                raise AssertionError("Unknown named vendors must not be semantically retrieved.")

        state = _state()
        state["messages"][0]["content"] = "Review Atlas Strategic Partners for GDPR obligations."
        result = create_legal_node(_NeverCallLLM(), _HighSimilarityUnknownVendorStore())(state)

        self.assertEqual(
            result["review_escalations"][0]["reason_code"],
            "unrecognized_vendor_identifier",
        )
        self.assertIn("Atlas Strategic Partners", result["messages"][0]["content"])

    def test_exact_vendor_match_is_not_treated_as_unknown_entity(self):
        class _VendorMetadataStore:
            def get(self, include):
                return {"metadatas": [{"vendor_name": "Atlas Strategic Partners"}]}

        self.assertIsNone(
            _unmatched_vendor_reference(
                _VendorMetadataStore(),
                "Review Atlas Strategic Partners for GDPR obligations.",
            )
        )

    def test_cited_cap_evidence_uses_a_deterministic_legal_summary(self):
        class _CapVectorStore:
            def get(self, include):
                return {"metadatas": [{"vendor_name": "Acme Ltd"}]}

            def similarity_search_with_relevance_scores(self, query, **kwargs):
                return [(
                    Document(
                        page_content="maximum monthly billing cap of INR 1,000",
                        metadata={
                            "filename": "acme.pdf",
                            "page_number": 2,
                            "vendor_name": "Acme Ltd",
                            "effective_date": "2024-01-01",
                            "expiry_date": "2024-12-31",
                        },
                    ),
                    0.9,
                )]

        state = _state()
        state["messages"][0]["content"] = "Are invoices over Acme Ltd's monthly cap?"
        result = create_legal_node(_NeverCallLLM(), _CapVectorStore())(state)

        self.assertIn("INR 1,000", result["messages"][0]["content"])
        self.assertEqual(result["context_data"]["contract_cap_terms"][0]["monthly_cap_inr"], 1000)

    def test_multiple_cited_cap_values_escalate_instead_of_selecting_or_prorating_one(self):
        class _MultiCapStore:
            def get(self, include, where=None):
                return {"metadatas": [{"vendor_name": "Acme Ltd"}]}

            def similarity_search_with_relevance_scores(self, _query, **_kwargs):
                return [(
                    Document(
                        page_content=(
                            "The maximum monthly billing cap of INR 10,000 applies until 2025-06-14. "
                            "The maximum monthly billing cap of INR 15,000 applies from 2025-06-15."
                        ),
                        metadata={
                            "filename": "acme-amendment.pdf",
                            "page_number": 2,
                            "vendor_name": "Acme Ltd",
                            "effective_date": "2025-01-01",
                            "expiry_date": "2025-12-31",
                        },
                    ),
                    0.9,
                )]

        state = _state()
        state["messages"][0]["content"] = "Is Acme Ltd compliant with its monthly cap in June 2025?"
        result = create_legal_node(_NeverCallLLM(), _MultiCapStore())(state)

        self.assertEqual(
            result["review_escalations"][0]["reason_code"],
            "ambiguous_effective_dated_cap_terms",
        )
        self.assertIn("cannot choose a cap or prorate", result["messages"][0]["content"])

    def test_vendor_bound_cap_fallback_adds_a_missed_cap_clause(self):
        class _VendorCapStore:
            def get(self, include, where=None):
                if where:
                    return {
                        "documents": ["maximum monthly billing cap of INR 2,500"],
                        "metadatas": [{"vendor_name": "Acme Ltd", "filename": "acme.pdf", "page_number": 2}],
                    }
                return {"metadatas": [{"vendor_name": "Acme Ltd"}]}

        results = _add_vendor_cap_evidence(
            _VendorCapStore(),
            "Are Acme Ltd payments compliant with its contract cap?",
            [],
        )

        self.assertEqual(len(results), 1)
        self.assertIn("INR 2,500", results[0][0].page_content)

    def test_standard_gdpr_and_penalty_checks_do_not_need_an_llm(self):
        class _ContractStore:
            def get(self, include, where=None):
                if where:
                    return {
                        "documents": [
                            "The Vendor complies with GDPR. A late payment fee of 24% per month applies."
                        ]
                    }
                return {"metadatas": [{"vendor_name": "Acme Ltd"}]}

        scored = [(
            Document(
                page_content="GDPR and late payment fee evidence.",
                metadata={"vendor_name": "Acme Ltd"},
            ),
            0.9,
        )]
        store = _ContractStore()

        gdpr = _deterministic_contract_analysis(store, "Does Acme Ltd meet GDPR?", scored)
        penalty = _deterministic_contract_analysis(store, "What late-payment penalty applies to Acme Ltd?", scored)

        self.assertIn("GDPR/data-protection", gdpr)
        self.assertIn("24%", penalty)
        self.assertIn("unusually high", penalty)

    def test_low_relevance_retrieval_abstains_without_llm_call(self):
        legal_node = create_legal_node(
            _NeverCallLLM(), _LowConfidenceVectorStore(), min_relevance_score=0.40
        )

        result = legal_node(_state())

        self.assertIn("ABSTAINED", result["messages"][0]["content"])
        self.assertEqual(result["iteration_count"], 1)
        self.assertEqual(result["context_data"]["abstentions"][0]["agent"], "legal_agent")
        escalation = result["review_escalations"][0]
        self.assertEqual(escalation["reason_code"], "low_retrieval_confidence")
        self.assertEqual(escalation["confidence"], 0.12)
        self.assertTrue(escalation["blocking"])

    def test_supervisor_finishes_after_a_specialist_abstains(self):
        state = _state()
        state["context_data"] = {
            "abstentions": [{"agent": "legal_agent", "reason": "low relevance"}]
        }
        state["review_escalations"] = [build_review_escalation(
            agent="legal_agent",
            reason_code="low_retrieval_confidence",
            reason="low relevance",
            recommended_action="Confirm the vendor name.",
            confidence=0.12,
            confidence_threshold=0.40,
        )]

        result = create_supervisor_node(_NeverCallLLM())(state)

        self.assertEqual(result["next_node"], "FINISH")
        self.assertIn("Human Review Required", result["final_report"])
        self.assertEqual(result["review_escalations"], [])

    def test_unverified_amendment_escalates_without_retrieval_or_llm_call(self):
        legal_node = create_legal_node(_NeverCallLLM(), _LowConfidenceVectorStore())
        state = _state()
        state["messages"][0]["content"] = (
            "An email says the contract was amended. Is the invoice spend compliant?"
        )

        result = legal_node(state)

        self.assertEqual(
            result["review_escalations"][0]["reason_code"],
            "missing_authoritative_document",
        )

    def test_underspecified_cap_question_escalates_before_llm_call(self):
        state = _state()
        state["messages"][0]["content"] = "Is the annual cap compliant?"

        result = create_supervisor_node(_NeverCallLLM())(state)

        self.assertEqual(result["next_node"], "FINISH")
        self.assertEqual(
            result["review_escalations"][0]["reason_code"],
            "missing_subject_or_period",
        )

    def test_exceeded_and_pay_alias_scope_questions_escalate_before_llm_call(self):
        for query in ("Is this billing cap exceeded?", "Can we pay M. M. Henderson under the monthly cap?"):
            state = _state()
            state["messages"][0]["content"] = query

            result = create_supervisor_node(_NeverCallLLM())(state)

            self.assertEqual(result["next_node"], "FINISH")
            self.assertEqual(
                result["review_escalations"][0]["reason_code"],
                "missing_subject_or_period",
            )

    def test_explicit_contract_cap_overpayment_routes_to_legal_without_llm(self):
        state = _state()
        state["iteration_count"] = 0
        state["messages"][0]["content"] = (
            "Are we overpaying Acme Ltd based on its contract monthly cap?"
        )

        result = create_supervisor_node(_NeverCallLLM())(state)

        self.assertEqual(result["next_node"], "legal_agent")

    def test_contract_evidence_question_routes_to_legal_without_router_llm(self):
        state = _state()
        state["iteration_count"] = 0
        state["messages"][0]["content"] = "Does Atlas Strategic Partners have a GDPR clause?"

        result = create_supervisor_node(_NeverCallLLM())(state)

        self.assertEqual(result["next_node"], "legal_agent")

    def test_late_payment_penalty_routes_to_legal_not_finance(self):
        state = _state()
        state["iteration_count"] = 0
        state["messages"][0]["content"] = "What late-payment penalty applies to Acme Ltd?"

        result = create_supervisor_node(_NeverCallLLM())(state)

        self.assertEqual(result["next_node"], "legal_agent")

    def test_contract_cap_evidence_routes_to_finance_without_llm(self):
        state = _state()
        state["iteration_count"] = 1
        state["messages"][0]["content"] = (
            "Are we overpaying Acme Ltd based on its contract monthly cap?"
        )
        state["context_data"] = {
            "contract_cap_terms": [{"vendor_name": "Acme Ltd", "monthly_cap_inr": 1000}]
        }

        result = create_supervisor_node(_NeverCallLLM())(state)

        self.assertEqual(result["next_node"], "finance_agent")

    def test_plural_invoice_cap_question_uses_the_cross_agent_path(self):
        state = _state()
        state["iteration_count"] = 0
        state["messages"][0]["content"] = (
            "Have invoices for Acme Ltd exceeded its contract billing cap?"
        )

        result = create_supervisor_node(_NeverCallLLM())(state)

        self.assertEqual(result["next_node"], "legal_agent")

    def test_unverified_side_letter_routes_to_legal_before_scope_heuristics(self):
        state = _state()
        state["iteration_count"] = 0
        state["messages"][0]["content"] = (
            "A side letter says Acme Ltd can exceed its monthly cap. Is its June spend compliant?"
        )

        result = create_supervisor_node(_NeverCallLLM())(state)

        self.assertEqual(result["next_node"], "legal_agent")

    def test_api_schema_preserves_a_structured_review_escalation(self):
        escalation = build_review_escalation(
            agent="legal_agent",
            reason_code="low_retrieval_confidence",
            reason="Low relevance.",
            recommended_action="Confirm the vendor name.",
            confidence=0.12,
            confidence_threshold=0.40,
        )

        response = QueryResponse(answer="## Human Review Required", review_escalations=[escalation])

        self.assertEqual(response.review_escalations[0].status, "human_review_required")
        self.assertEqual(response.review_escalations[0].reason_code, "low_retrieval_confidence")
