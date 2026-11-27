"""
Supervisor Agent Node
======================
LLM-powered routing node that analyzes user queries and delegates
to the appropriate specialist agent (Legal or Finance).
"""

import json
import re
from langchain_core.messages import SystemMessage, HumanMessage
from app.agents.state import AgentState
from app.agents.review import build_review_escalation
from app.agents.llm_utils import response_text


SUPERVISOR_SYSTEM_PROMPT = """You are an Enterprise Operations Supervisor AI for a global corporation.
Your role is to analyze incoming queries and route them to the appropriate specialist agent.

You have access to two specialist agents:
1. **legal_agent**: A compliance auditor that searches vendor contracts (PDFs) stored in a vector database.
   Route here for questions about: contract terms, GDPR compliance, penalty clauses, termination conditions,
   vendor obligations, regulatory compliance, data protection clauses.

2. **finance_agent**: A financial analyst with read-only SQL access to the invoice/payment database.
   Route here for questions about: payments, invoices, spending, overdue amounts, vendor costs,
   payment trends, duplicate payments, budget analysis.

For complex cross-departmental queries (e.g., "Are we overpaying Vendor X based on their contract?"),
you should route to BOTH agents sequentially — first to legal_agent to get contract terms,
then to finance_agent to check actual payments.

RESPONSE FORMAT: You must respond with valid JSON containing exactly one field:
{
    "next_agent": "legal_agent" | "finance_agent" | "FINISH"
}

- Use "FINISH" only when you have enough information from the agents to compile a final answer.
- If the query doesn't relate to contracts or finances, respond with "FINISH" and explain in the messages.
- Maximum routing iterations: 4 (to prevent infinite loops).
"""


def create_supervisor_node(llm):
    """Create the supervisor node function with the given LLM."""

    def supervisor_node(state: AgentState) -> dict:
        """Supervisor node: routes to the appropriate agent or finishes."""
        messages = state.get("messages", [])
        context = state.get("context_data", {})
        iteration = state.get("iteration_count", 0)
        review_escalations = state.get("review_escalations", [])

        # A monthly/annual cap question with no subject is not an evidence
        # retrieval problem: it is under-specified. Stop before spending model
        # calls or combining unrelated contract and ledger records.
        query = _extract_user_query(messages)
        # Alleged amendments, waivers, drafts, and similar documents must be
        # reviewed by Legal before generic scope heuristics. This preserves the
        # more specific evidence-gap reason instead of treating it as merely a
        # missing-period request.
        if not review_escalations and not _requires_authoritative_document(query) and _needs_scope_clarification(query):
            escalation = build_review_escalation(
                agent="supervisor",
                reason_code="missing_subject_or_period",
                reason=(
                    "A payment-cap conclusion needs an identified vendor and the relevant "
                    "contract period; the request does not provide enough scope."
                ),
                recommended_action=(
                    "Provide the vendor's legal name and the month or date range to compare "
                    "against the applicable contract cap."
                ),
            )
            return _review_finish(state, [escalation], iteration + 1)

        if review_escalations:
            return _review_finish(state, review_escalations, iteration + 1)

        if iteration == 0 and _requires_authoritative_document(query):
            return _route_response("legal_agent", context, iteration)

        abstentions = context.get("abstentions", [])
        if abstentions:
            reasons = "\n".join(
                f"- {item.get('agent', 'agent')}: {item.get('reason', 'insufficient evidence')}"
                for item in abstentions
            )
            return {
                "next_node": "FINISH",
                "final_report": (
                    "## Insufficient Reliable Evidence\n\n"
                    "The system is declining to make a compliance or payment conclusion. "
                    "Please provide a more specific vendor, contract, or date range.\n\n"
                    f"### Reason\n{reasons}"
                ),
                "iteration_count": iteration + 1,
                "context_data": context,
                "messages": [{
                    "role": "supervisor",
                    "content": "Finishing because a specialist abstained due to insufficient evidence.",
                }],
                "cited_sources": [],
                "sql_queries": [],
                "error_log": [],
                "review_escalations": [],
            }

        # Route to Risk Synthesizer once both specialist agents have contributed
        if (
            _both_agents_have_run(messages)
            and not _risk_synthesizer_already_ran(messages)
            and not context.get("abstentions")
        ):
            return _route_response("risk_synthesizer", context, iteration)

        # Guard: prevent infinite loops
        if iteration >= 5:
            return {
                "next_node": "FINISH",
                "final_report": _compile_final_report(state),
                "iteration_count": iteration,
                "messages": [{
                    "role": "system",
                    "content": "Maximum iteration limit reached. Compiling final report."
                }],
                "cited_sources": [],
                "sql_queries": [],
                "error_log": [],
                "review_escalations": [],
            }

        # Explicit contract-cap-overpayment questions have a fixed evidence
        # plan. Do not let a probabilistic routing response skip one of the
        # two required evidence sources; reserve LLM routing for ambiguous
        # questions and final synthesis.
        if iteration == 0 and _is_contract_cap_overpayment_question(query):
            return _route_response("legal_agent", context, iteration)
        if (
            context.get("contract_cap_terms")
            and "finance_findings" not in context
            and _is_contract_cap_overpayment_question(query)
        ):
            return _route_response("finance_agent", context, iteration)
        if context.get("finance_findings") and _is_contract_cap_overpayment_question(query):
            return {
                "next_node": "FINISH",
                "final_report": _compile_final_report(state),
                "iteration_count": iteration + 1,
                "context_data": context,
                "messages": [{
                    "role": "supervisor",
                    "content": "Compiled contract-cap evidence with the financial analysis.",
                }],
                "cited_sources": [],
                "sql_queries": [],
                "error_log": [],
                "review_escalations": [],
            }

        # Common ledger-only audit metrics have one evidence source. Route and
        # finish deterministically so free-tier supervisor availability cannot
        # transform a finance question into an empty final report.
        if iteration == 0 and _is_finance_evidence_question(query):
            return _route_response("finance_agent", context, iteration)
        if (
            iteration > 0
            and _is_finance_evidence_question(query)
            and any(message.get("role") == "finance_agent" for message in messages)
        ):
            return {
                "next_node": "FINISH",
                "final_report": _compile_final_report(state),
                "iteration_count": iteration + 1,
                "context_data": context,
                "messages": [{
                    "role": "supervisor",
                    "content": "Compiled the evidence-bounded financial analysis.",
                }],
                "cited_sources": [],
                "sql_queries": [],
                "error_log": [],
                "review_escalations": [],
            }

        # Contract-evidence questions have one appropriate first stop. Route
        # them deterministically so an unavailable free-tier router cannot
        # turn an evidence-bound question into an unsupported answer. The
        # legal specialist can still use the configured LLM for open-ended
        # analysis after its retrieval and evidence gates pass.
        if iteration == 0 and _is_contract_evidence_question(query):
            return _route_response("legal_agent", context, iteration)
        if (
            iteration > 0
            and _is_contract_evidence_question(query)
            and any(message.get("role") == "legal_agent" for message in messages)
        ):
            return {
                "next_node": "FINISH",
                "final_report": _compile_final_report(state),
                "iteration_count": iteration + 1,
                "context_data": context,
                "messages": [{
                    "role": "supervisor",
                    "content": "Compiled the evidence-bounded legal analysis.",
                }],
                "cited_sources": [],
                "sql_queries": [],
                "error_log": [],
                "review_escalations": [],
            }

        # Build the routing prompt
        routing_messages = [
            SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT),
        ]

        # Add conversation context
        for msg in messages:
            if msg["role"] == "user":
                routing_messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] in ("legal_agent", "finance_agent"):
                routing_messages.append(HumanMessage(
                    content=f"[{msg['role']} response]: {msg['content']}"
                ))

        # If we have context from previous agents, include it
        if context:
            context_summary = json.dumps(context, indent=2, default=str)
            routing_messages.append(HumanMessage(
                content=f"[Context gathered so far]:\n{context_summary}\n\n"
                        f"Based on the above, decide the next step. "
                        f"If enough information is available, respond with FINISH."
            ))

        # Ask LLM for routing decision
        try:
            response = llm.invoke(routing_messages)
            content = response_text(response).strip()

            # Parse JSON response
            # Handle cases where LLM wraps JSON in markdown code blocks
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

            decision = json.loads(content)
            next_agent = decision.get("next_agent", "FINISH")

            # Validate
            if next_agent not in ("legal_agent", "finance_agent", "FINISH"):
                next_agent = "FINISH"

        except (json.JSONDecodeError, Exception) as e:
            # Fallback: try to extract agent name from free text
            content_lower = content.lower() if 'content' in dir() else ""
            if "legal" in content_lower:
                next_agent = "legal_agent"
            elif "finance" in content_lower or "sql" in content_lower:
                next_agent = "finance_agent"
            else:
                next_agent = "FINISH"

        # If finishing, compile the final report
        final_report = ""
        if next_agent == "FINISH":
            final_report = _compile_final_report(state)

        return {
            "next_node": next_agent,
            "final_report": final_report,
            "iteration_count": iteration + 1,
            "context_data": context,
            "messages": [{
                "role": "supervisor",
                "content": f"Routing to: {next_agent}"
            }],
            "cited_sources": [],
            "sql_queries": [],
            "error_log": [],
            "review_escalations": [],
        }

    return supervisor_node


def _compile_final_report(state: AgentState) -> str:
    """Compile all agent responses into a coherent final report."""
    messages = state.get("messages", [])
    sources = state.get("cited_sources", [])
    queries = state.get("sql_queries", [])

    # Collect agent responses
    agent_responses = []
    for msg in messages:
        if msg["role"] in ("legal_agent", "finance_agent"):
            agent_responses.append(f"**{msg['role'].replace('_', ' ').title()}**:\n{msg['content']}")

    if not agent_responses:
        return "I was unable to gather sufficient information to answer your query. Please rephrase or provide more details."

    report_parts = ["## Analysis Report\n"]
    report_parts.extend(agent_responses)

    if sources:
        report_parts.append("\n### Sources Cited")
        for s in sources:
            report_parts.append(f"- **{s.get('filename', 'Unknown')}**, Page {s.get('page_number', '?')}: {s.get('clause', '')}")

    if queries:
        report_parts.append("\n### SQL Queries Executed")
        for q in queries:
            report_parts.append(f"```sql\n{q}\n```")

    return "\n\n".join(report_parts)


def _extract_user_query(messages: list[dict]) -> str:
    for message in messages:
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


def _needs_scope_clarification(query: str) -> bool:
    """Identify a cap-compliance request whose subject/period is missing."""
    normalized = query.lower()
    asks_for_determination = bool(re.search(
        r"\b(compliant|compliance|exceed(?:ed|s|ing)?|overpay(?:ing|ment)?|"
        r"breach(?:ed|es|ing)?|pay|approve)\b",
        normalized,
    ))
    vague_cap = bool(re.search(
        r"\b(the|its|this)\s+(annual|monthly|billing)?\s*cap\b",
        normalized,
    ))
    ambiguous_initials = bool(re.search(r"\b[a-z]\.\s*[a-z]\.\s+[a-z]+\b", normalized))
    return asks_for_determination and (vague_cap or ambiguous_initials)


def _is_contract_cap_overpayment_question(query: str) -> bool:
    normalized = query.lower()
    mentions_contract = bool(re.search(r"\b(contract|agreement|billing\s+cap|monthly\s+cap|cap)\b", normalized))
    mentions_finance = bool(re.search(
        r"\b(overpay(?:ing|ment)?|payments?|invoices?|spend|cost|billing|ledger)\b",
        normalized,
    ))
    return mentions_contract and mentions_finance


def _is_finance_evidence_question(query: str) -> bool:
    """Identify ledger-only questions that should not need an LLM router."""
    # Contract-language questions such as a "late-payment penalty" can contain
    # finance words but require legal evidence and citations, not ledger SQL.
    if re.search(
        r"\b(contract|agreement|gdpr|data[-\s]?protection|termination|"
        r"penalty|late[-\s]?fee|late[-\s]?payment|vendor\s+obligation)\b",
        query,
        re.IGNORECASE,
    ):
        return False
    return bool(re.search(
        r"\b(invoice|invoices|payment|payments|spend|cost|overdue|duplicate|"
        r"average\s+invoice|ledger)\b",
        query,
        re.IGNORECASE,
    ))


def _requires_authoritative_document(query: str) -> bool:
    """Identify a material claim that cannot be established by base RAG alone."""
    document_signal = (
        r"\b(amended|amendment|side\s+letter|waiver|draft|email|"
        r"change\s+order|verbal\s+agreement)\b"
    )
    material_signal = (
        r"\b(cap|compliance|compliant|overpay|exceed|invoice|payment|"
        r"obligation|gdpr|termination|penalty|term)\b"
    )
    return bool(re.search(document_signal, query, re.IGNORECASE) and re.search(material_signal, query, re.IGNORECASE))


def _is_contract_evidence_question(query: str) -> bool:
    """Identify questions whose first evidence source must be the contract corpus."""
    return bool(re.search(
        r"\b(contract|agreement|gdpr|data[-\s]?protection|termination|"
        r"penalty|late[-\s]?fee|vendor\s+obligation)\b",
        query,
        re.IGNORECASE,
    ))


def _route_response(next_agent: str, context: dict, iteration: int) -> dict:
    return {
        "next_node": next_agent,
        "final_report": "",
        "iteration_count": iteration + 1,
        "context_data": context,
        "messages": [{
            "role": "supervisor",
            "content": f"Routing to: {next_agent} (deterministic contract-cap evidence plan)",
        }],
        "cited_sources": [],
        "sql_queries": [],
        "error_log": [],
        "review_escalations": [],
    }


def _review_finish(state: AgentState, escalations: list[dict], iteration: int) -> dict:
    """Finish with a displayable, auditable human-review handoff."""
    lines = [
        "## Human Review Required",
        "",
        "The system is declining to make a compliance or payment conclusion from incomplete or unverified evidence.",
        "",
        "### Review reasons",
    ]
    for escalation in escalations:
        confidence = escalation.get("confidence")
        confidence_note = f" (retrieval confidence: {confidence:.2f})" if isinstance(confidence, (float, int)) else ""
        lines.append(
            f"- **{escalation.get('agent', 'agent')}** — {escalation.get('reason', 'Insufficient evidence.')}{confidence_note}"
        )
    lines.extend(["", "### Recommended action"])
    for escalation in escalations:
        lines.append(f"- {escalation.get('recommended_action', 'Provide authoritative supporting evidence.')}")

    existing_escalations = state.get("review_escalations", [])
    new_escalations = [
        escalation for escalation in escalations if escalation not in existing_escalations
    ]

    return {
        "next_node": "FINISH",
        "final_report": "\n".join(lines),
        "iteration_count": iteration,
        "context_data": state.get("context_data", {}),
        "messages": [{
            "role": "supervisor",
            "content": "Escalating to human review because a material conclusion is not evidence-supported.",
        }],
        "cited_sources": [],
        "sql_queries": [],
        "error_log": [],
        "review_escalations": new_escalations,
    }


def _both_agents_have_run(messages: list[dict]) -> bool:
    """Check if both legal and finance agents have contributed to this conversation."""
    roles = {m.get("role") for m in messages}
    return "legal_agent" in roles and "finance_agent" in roles


def _risk_synthesizer_already_ran(messages: list[dict]) -> bool:
    """Check if the risk synthesizer node has already run in this conversation."""
    return any(m.get("role") == "risk_synthesizer" for m in messages)
