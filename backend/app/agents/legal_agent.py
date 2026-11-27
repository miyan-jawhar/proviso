"""
Legal Agent (RAG)
==================
Compliance auditor agent that searches vendor contracts in ChromaDB.
Outputs source citations (filename, page number, relevant clause) for XAI.
"""

import re

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.documents import Document
from app.agents.state import AgentState
from app.agents.review import build_review_escalation
from app.agents.llm_utils import response_text


LEGAL_SYSTEM_PROMPT = """You are an Enterprise Legal Compliance Auditor AI.
Your role is to analyze vendor contracts and identify compliance risks.

You have access to a vector database containing enterprise vendor contracts.
When answering questions, you MUST:

1. Search the contract database for relevant clauses
2. Always cite your sources: filename, page number, and the exact clause text
3. Flag any compliance risks found:
   - Missing GDPR/data protection clauses
   - Unusually high penalty fees (anything above 10% is suspicious)
   - Missing or weak termination protections
   - Regulatory non-compliance indicators

4. Provide your response in this structured format:
   FINDINGS: [Your analysis]
   RISK_LEVEL: [LOW / MEDIUM / HIGH / CRITICAL]
   SOURCES: [List of source documents with page numbers]

Be precise and factual. Only state what the contracts actually say.
Do not speculate or make assumptions about terms not present in the documents.
For a billing-cap question, quote the cap, identify its monthly cadence, and
state the cited contract page. Do not convert the cap into an invoice average.
"""

# Matches multi-currency monthly cap clauses, e.g.:
#   "maximum monthly billing cap of INR 5,00,000"
#   "not to exceed $50,000 per month"
#   "maximum monthly fee of £200,000"
#   "not-to-exceed amount of EUR 62,000 per month"
MONTHLY_CAP_PATTERN = re.compile(
    r"""
    (?:
        # Pattern A: "cap/limit/fee/ceiling of [CURRENCY] AMOUNT"
        # Handles: "maximum monthly billing cap of USD 45,000"
        #          "not-to-exceed amount of EUR 62,000 per month"
        #          "maximum monthly fee of £85,000"
        (?:maximum\s+|not[\s\-]to[\s\-]exceed\s+)?
        (?:monthly\s+)?
        (?:billing\s+|service\s+|payment\s+|not[\s\-]to[\s\-]exceed\s+)?
        (?:cap|limit|fee|amount|ceiling)
        \s+of\s+
        (?:(?:INR|USD|GBP|EUR|AUD|CAD)\s*)?   # optional ISO code
        (?:£|\$|€|₹|Rs\.?)?\s*                 # optional symbol
        ([0-9][0-9,]*(?:\.[0-9]+)?)
        (?:\s*(?:thousand|million|k|M|lakh|crore))?
    |
        # Pattern B: "not to exceed USD X [per month]"
        # Handles: "total monthly not-to-exceed amount shall be USD 78,000 per month"
        not[\s\-]to[\s\-]exceed\s+
        (?:(?:INR|USD|GBP|EUR|AUD|CAD)\s*)?
        (?:£|\$|€|₹|Rs\.?)?\s*
        ([0-9][0-9,]*(?:\.[0-9]+)?)
        (?:\s*(?:thousand|million|k|M|lakh|crore))?
        (?:\s+per\s+month)?
    |
        # Pattern B2: "shall be USD 78,000 per month" (amount after currency code)
        shall\s+be\s+
        (?:INR|USD|GBP|EUR|AUD|CAD)\s*
        (?:£|\$|€|₹|Rs\.?)?\s*
        ([0-9][0-9,]*(?:\.[0-9]+)?)
        (?:\s*(?:thousand|million|k|M|lakh|crore))?
        \s+per\s+month
    |
        # Pattern C: original "monthly cap of INR X" (backward compat)
        (?:maximum\s+)?monthly\s+(?:billing\s+)?cap\s+of\s+
        (?:(?:INR|USD|GBP|EUR)\s*)?
        (?:£|\$|€|₹|Rs\.?)?\s*
        ([0-9][0-9,]*(?:\.[0-9]+)?)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _extract_cap_amount(match: re.Match) -> int | None:
    """Extract the numeric cap amount from any MONTHLY_CAP_PATTERN match group."""
    for group in match.groups():
        if group:
            raw = group.replace(",", "")
            try:
                return int(float(raw))
            except ValueError:
                return None
    return None


def create_legal_node(llm, vectorstore, min_relevance_score: float = 0.40):
    """Create the Legal Agent node with calibrated ChromaDB retrieval."""

    def legal_node(state: AgentState) -> dict:
        """Legal Agent: searches contracts and analyzes compliance."""
        messages = state.get("messages", [])
        context = state.get("context_data", {})

        # Extract the query from messages
        query = _extract_query(messages)
        if not query:
            return _abstain_response(
                state,
                reason="No query found for legal analysis.",
                reason_code="missing_query",
                recommended_action="Provide the vendor, contract, and compliance question to review.",
            )

        # The indexed contract corpus cannot establish whether an external
        # amendment, side letter, draft, or waiver is the authoritative term.
        # Treat those claims as an evidence gap instead of letting a retrieved
        # base contract silently override them.
        if _has_unverified_material_document_claim(query):
            return _abstain_response(
                state,
                reason=(
                    "The question relies on an amendment, draft, side letter, waiver, "
                    "or other document that is not verified in the indexed contract corpus."
                ),
                reason_code="missing_authoritative_document",
                recommended_action=(
                    "Attach or identify the executed amendment and effective date before "
                    "making a compliance or payment-cap determination."
                ),
            )

        # A fully named vendor is a deterministic scope constraint, not an LLM
        # inference. Bind it to Chroma metadata before semantic retrieval so a
        # generic phrase such as "overpaying" cannot displace the requested
        # vendor's contract with an unrelated payment-terms passage.
        retrieval_query, search_kwargs = _build_retrieval_plan(vectorstore, query)
        unmatched_vendor = _unmatched_vendor_reference(vectorstore, query)
        if unmatched_vendor:
            return _abstain_response(
                state,
                reason=(
                    f"'{unmatched_vendor}' does not match an unambiguous legal vendor "
                    "identifier in the indexed contract corpus."
                ),
                reason_code="unrecognized_vendor_identifier",
                recommended_action=(
                    "Provide the vendor's legal name, an approved alias mapping, or the "
                    "executed contract before making a compliance determination."
                ),
            )

        # Retrieve relevance scores rather than treating every top-k result as
        # evidence. The threshold is configurable and must be calibrated with
        # the evaluation suite before production use.
        try:
            scored_results = vectorstore.similarity_search_with_relevance_scores(
                retrieval_query, **search_kwargs
            )
        except Exception as e:
            return _abstain_response(
                state,
                reason=f"Vector database search failed: {str(e)}",
                reason_code="retrieval_unavailable",
                recommended_action="Restore contract-index access and rerun the review.",
            )

        # A vendor-bound cap decision needs the actual cap clause, not merely
        # a semantically related payment paragraph. If the top-k search missed
        # that clause, inspect the already-filtered vendor documents and add
        # the cited cap passage as deterministic evidence.
        scored_results = _add_vendor_cap_evidence(vectorstore, query, scored_results)

        if not scored_results:
            return _abstain_response(
                state,
                reason="No contract passages were retrieved, so the legal agent cannot answer reliably.",
                reason_code="no_contract_evidence",
                recommended_action="Provide the executed contract or a more specific vendor identifier.",
            )

        top_score = float(scored_results[0][1])
        if top_score < min_relevance_score:
            return _abstain_response(
                state,
                reason=(
                    "Top contract retrieval relevance "
                    f"({top_score:.2f}) is below the configured confidence threshold "
                    f"({min_relevance_score:.2f})."
                ),
                reason_code="low_retrieval_confidence",
                recommended_action=(
                    "Confirm the vendor name or upload the authoritative contract before "
                    "making a compliance determination."
                ),
                confidence=top_score,
                confidence_threshold=min_relevance_score,
            )

        # Build context from search results
        sources = []
        context_texts = []
        for i, (doc, score) in enumerate(scored_results):
            meta = doc.metadata
            sources.append({
                "filename": meta.get("filename", "Unknown"),
                "page_number": meta.get("page_number", "?"),
                "vendor_name": meta.get("vendor_name", "Unknown"),
                "clause": doc.page_content[:200],
                "relevance_rank": i + 1,
                "relevance_score": round(float(score), 4),
            })
            context_texts.append(
                f"[Source {i+1}: {meta.get('filename', '?')}, "
                f"Page {meta.get('page_number', '?')}, "
                f"Vendor: {meta.get('vendor_name', '?')}]\n"
                f"{doc.page_content}\n"
            )

        contract_cap_terms = _extract_contract_cap_terms(scored_results)
        if _is_monthly_cap_question(query) and _has_conflicting_monthly_cap_values(contract_cap_terms):
            return _abstain_response(
                state,
                reason=(
                    "The cited contract evidence contains more than one monthly cap value for "
                    "the same vendor. Proviso cannot choose a cap or prorate a calendar month "
                    "without an authoritative effective-dated cap schedule."
                ),
                reason_code="ambiguous_effective_dated_cap_terms",
                recommended_action=(
                    "Provide the executed amendment or an approved effective-dated cap schedule, "
                    "including the policy for months in which the cap changes."
                ),
            )
        # The cap amount, cadence, and source page are explicitly structured
        # in retrieved evidence. Preserve that deterministic evidence for a
        # cap-compliance request instead of spending a free-tier model call to
        # restate it; Gemini remains the analyst for open-ended legal reviews.
        deterministic_analysis = _deterministic_contract_analysis(
            vectorstore, query, scored_results
        )
        if _is_monthly_cap_question(query) and contract_cap_terms:
            analysis = _format_contract_cap_analysis(contract_cap_terms)
        elif deterministic_analysis:
            analysis = deterministic_analysis
        else:
            analysis_messages = [
                SystemMessage(content=LEGAL_SYSTEM_PROMPT),
                HumanMessage(content=(
                    f"QUERY: {query}\n\n"
                    f"RETRIEVED CONTRACT DOCUMENTS:\n"
                    f"{'---'.join(context_texts)}\n\n"
                    f"Analyze the above documents and provide your compliance assessment."
                )),
            ]

            try:
                response = llm.invoke(analysis_messages)
                analysis = response_text(response)
            except Exception as e:
                analysis = f"LLM analysis failed: {str(e)}"

        # Update shared context with legal findings
        updated_context = dict(context)
        updated_context["legal_findings"] = analysis
        updated_context["legal_sources"] = sources
        updated_context["retrieval_confidence"] = round(top_score, 4)
        if contract_cap_terms:
            updated_context["contract_cap_terms"] = contract_cap_terms

        # Extract vendor-specific data if mentioned
        for src in sources:
            vendor = src.get("vendor_name", "")
            if vendor and vendor != "Unknown":
                updated_context.setdefault("vendors_analyzed", [])
                if vendor not in updated_context["vendors_analyzed"]:
                    updated_context["vendors_analyzed"].append(vendor)

        return {
            "messages": [{
                "role": "legal_agent",
                "content": analysis,
            }],
            "context_data": updated_context,
            "cited_sources": sources,
            "sql_queries": [],
            "error_log": [],
            "review_escalations": [],
            "next_node": "supervisor",
            "final_report": "",
            "iteration_count": state.get("iteration_count", 0),
        }

    return legal_node


def _extract_query(messages: list[dict]) -> str:
    """Extract the user's original query from messages."""
    for msg in reversed(messages):
        if msg["role"] == "user":
            return msg["content"]
    # Fallback: use last message content
    if messages:
        return messages[-1].get("content", "")
    return ""


def _has_unverified_material_document_claim(query: str) -> bool:
    """Return true for terms that require an authoritative source outside RAG."""
    document_signal = (
        r"\b(amended|amendment|side\s+letter|waiver|draft|email|"
        r"change\s+order|verbal\s+agreement)\b"
    )
    material_decision = (
        r"\b(cap|compliance|compliant|overpay|exceed|invoice|payment|"
        r"obligation|gdpr|termination|penalty|term)\b"
    )
    return bool(re.search(document_signal, query, re.IGNORECASE) and re.search(material_decision, query, re.IGNORECASE))


def _matched_vendor_name(vectorstore, query: str) -> str | None:
    """Return an exact normalized legal vendor name from an explicit name span.

    The previous token-subset matcher could bind ``Doyle Limited`` to
    ``Doyle Ltd`` or ``Davis and Sons India`` to ``Davis and Sons`` because it
    looked across the whole query.  Entity names need a stricter boundary than
    generic query words: punctuation and ``&``/``and`` normalize, but an added
    legal suffix, DBA, subsidiary, or other material name token must be backed
    by an approved alias mapping instead of semantic retrieval.
    """
    try:
        stored = vectorstore.get(include=["metadatas"])
        metadatas = stored.get("metadatas", [])
    except Exception:
        return None

    vendor_names = {
        metadata.get("vendor_name")
        for metadata in metadatas
        if isinstance(metadata, dict) and metadata.get("vendor_name")
    }
    vendor_profiles = {
        vendor_name: _vendor_name_profile(vendor_name)
        for vendor_name in vendor_names
    }
    matches: set[str] = set()
    for candidate in _vendor_name_candidates(query):
        candidate_profile = _vendor_name_profile(candidate)
        if not candidate_profile[0]:
            continue
        for vendor_name, vendor_profile in vendor_profiles.items():
            if candidate_profile == vendor_profile:
                matches.add(vendor_name)

    return next(iter(matches)) if len(matches) == 1 else None


def _build_retrieval_plan(vectorstore, query: str) -> tuple[str, dict]:
    """Build the same vendor-bound retrieval request used by the legal agent."""
    vendor_name = _matched_vendor_name(vectorstore, query)
    if not vendor_name:
        return query, {"k": 6}

    retrieval_query = f"{vendor_name} contract terms relevant to: {query}"
    if _is_monthly_cap_question(query):
        retrieval_query += " monthly billing cap"
    return retrieval_query, {"k": 6, "filter": {"vendor_name": vendor_name}}


def _add_vendor_cap_evidence(vectorstore, query: str, scored_results: list[tuple]) -> list[tuple]:
    """Ensure a named vendor's cited cap clause is available for cap decisions."""
    if not _is_monthly_cap_question(query) or _extract_contract_cap_terms(scored_results):
        return scored_results

    vendor_name = _matched_vendor_name(vectorstore, query)
    if not vendor_name:
        return scored_results
    try:
        stored = vectorstore.get(
            where={"vendor_name": vendor_name},
            include=["documents", "metadatas"],
        )
    except Exception:
        return scored_results

    documents = stored.get("documents", [])
    metadatas = stored.get("metadatas", [])
    for page_content, metadata in zip(documents, metadatas):
        if page_content and MONTHLY_CAP_PATTERN.search(page_content):
            return [*scored_results, (Document(page_content=page_content, metadata=metadata), 1.0)]
    return scored_results


def _unmatched_vendor_reference(vectorstore, query: str) -> str | None:
    """Return an obvious named vendor reference that is absent from the corpus.

    Semantic similarity cannot establish that a question about *Atlas Strategic
    Partners* is evidence about a different, similar-looking vendor contract.
    This is deliberately a narrow, deterministic entity gate rather than a
    general-purpose NER model: it recognizes common contract-review phrasings
    and requires an exact unambiguous metadata match before legal analysis.
    """
    # Do not claim entity validation when the metadata inventory itself is
    # unavailable. The regular retrieval-confidence path handles that case.
    try:
        stored = vectorstore.get(include=["metadatas"])
        if not stored.get("metadatas", []):
            return None
    except Exception:
        return None

    if _matched_vendor_name(vectorstore, query):
        return None

    ignored = {"GDPR", "SQL", "INR"}
    for candidate in _vendor_name_candidates(query):
        if _vendor_name_profile(candidate)[0] and candidate.upper() not in ignored:
            return candidate
    return None


_NAME_SPAN_PATTERN = re.compile(
    r"\b[A-Z][A-Za-z0-9&.'-]*(?:\s*,?\s+(?:[A-Z][A-Za-z0-9&.'-]*|and|&))*"
)
_QUERY_PREFIXES = {
    "are", "assess", "audit", "check", "compare", "does", "find", "have",
    "is", "review", "show", "what",
}
_NAME_CONNECTORS = {"and", "the"}
_LEGAL_DESIGNATORS = {
    "ag", "bv", "co", "company", "corp", "corporation", "gmbh", "group",
    "inc", "incorporated", "limited", "llc", "llp", "lp", "ltd", "plc",
    "private", "pvt", "sa",
}


def _vendor_name_candidates(query: str) -> list[str]:
    """Extract capitalized, user-supplied name spans without doing NER by LLM."""
    candidates: list[str] = []
    seen: set[str] = set()
    for match in _NAME_SPAN_PATTERN.finditer(query):
        candidate = re.sub(r"(?i)'s\b", "", match.group(0)).strip(" ,.")
        tokens = re.findall(r"[A-Za-z0-9]+", candidate)
        while tokens and tokens[0].lower() in _QUERY_PREFIXES:
            tokens.pop(0)
        if not tokens:
            continue
        normalized = " ".join(tokens)
        if normalized.lower() not in seen:
            candidates.append(normalized)
            seen.add(normalized.lower())
    return candidates


def _vendor_name_profile(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """Normalize harmless punctuation but preserve legal-entity distinctions."""
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    legal_designators = frozenset(tokens & _LEGAL_DESIGNATORS)
    base_tokens = frozenset(tokens - _NAME_CONNECTORS - _LEGAL_DESIGNATORS)
    return base_tokens, legal_designators


def _is_monthly_cap_question(query: str) -> bool:
    return bool(re.search(
        r"\b(monthly\s+)?cap\b|\boverpay(?:ment|ing)?\b|\bbilling\s+limit\b",
        query,
        re.IGNORECASE,
    ))


def _format_contract_cap_analysis(contract_cap_terms: list[dict]) -> str:
    lines = ["FINDINGS:"]
    for term in contract_cap_terms:
        amount = term.get("monthly_cap_amount") or term.get("monthly_cap_inr", 0)
        lines.append(
            "- The cited contract sets a maximum monthly billing cap of "
            f"INR {amount:,} for {term['vendor_name']} "
            f"(source: {term['source_file']}, page {term['page_number']}; "
            f"term: {term['effective_date']} to {term['expiry_date']})."
        )
    lines.extend([
        "- This evidence establishes a monthly aggregate comparison policy; "
        "the finance agent must compare the sum of invoices in each calendar month.",
        "RISK_LEVEL: MEDIUM",
    ])
    return "\n".join(lines)


def _deterministic_contract_analysis(vectorstore, query: str, scored_results: list[tuple]) -> str | None:
    """Answer standard GDPR and late-fee checks from cited contract text.

    These are literal contract-fact checks, so a model adds availability risk
    without improving the result. Open-ended legal interpretation remains on
    the configured LLM path below.
    """
    if not (_is_gdpr_question(query) or _is_penalty_question(query)):
        return None

    vendor_name = _matched_vendor_name(vectorstore, query)
    if not vendor_name:
        return None
    corpus = _vendor_contract_text(vectorstore, vendor_name)
    if not corpus:
        corpus = "\n".join(document.page_content for document, _ in scored_results)
    normalized = corpus.lower()

    if _is_gdpr_question(query):
        has_gdpr = bool(re.search(
            r"\bgdpr\b|general\s+data\s+protection\s+regulation|"
            r"data\s+protection",
            normalized,
        ))
        finding = (
            "The cited contract contains GDPR/data-protection language."
            if has_gdpr
            else "No GDPR/data-protection clause was found in the cited contract text."
        )
        return "\n".join([
            "FINDINGS:",
            f"- Vendor: {vendor_name}",
            f"- {finding}",
            f"RISK_LEVEL: {'LOW' if has_gdpr else 'HIGH'}",
        ])

    penalty_match = re.search(
        r"late\s+payment\s+(?:fee|penalty)\s+of\s+(\d+(?:\.\d+)?%)|"
        r"late[-\s]?fee\s+(?:of\s+)?(\d+(?:\.\d+)?%)",
        normalized,
    )
    percentage = next((group for group in penalty_match.groups() if group), None) if penalty_match else None
    if percentage:
        rate = float(percentage.rstrip("%"))
        assessment = "unusually high" if rate > 10 else "within the prototype's normal range"
        finding = f"The cited late-payment penalty is {percentage} per month, which is {assessment}."
        risk_level = "HIGH" if rate > 10 else "LOW"
    else:
        finding = "No explicit late-payment penalty percentage was found in the cited contract text."
        risk_level = "MEDIUM"
    return "\n".join([
        "FINDINGS:",
        f"- Vendor: {vendor_name}",
        f"- {finding}",
        f"RISK_LEVEL: {risk_level}",
    ])


def _vendor_contract_text(vectorstore, vendor_name: str) -> str:
    """Read all already-indexed chunks for one exact vendor, without semantic inference."""
    try:
        stored = vectorstore.get(
            where={"vendor_name": vendor_name},
            include=["documents"],
        )
    except Exception:
        return ""
    return "\n".join(document for document in stored.get("documents", []) if document)


def _is_gdpr_question(query: str) -> bool:
    return bool(re.search(r"\bgdpr\b|data[-\s]?protection", query, re.IGNORECASE))


def _is_penalty_question(query: str) -> bool:
    return bool(re.search(r"penalty|late[-\s]?fee|late[-\s]?payment", query, re.IGNORECASE))


def _extract_contract_cap_terms(scored_results: list[tuple]) -> list[dict]:
    """Carry cited monthly-cap evidence into the finance step without LLM parsing.

    Supports multi-currency contracts (USD, GBP, EUR, INR) via the generalised
    MONTHLY_CAP_PATTERN. The key ``monthly_cap_inr`` is kept for backward
    compatibility with the finance agent and evaluation suite; the actual
    currency may differ for real-contract fixtures.
    """
    terms = []
    seen = set()
    for document, _score in scored_results:
        for match in MONTHLY_CAP_PATTERN.finditer(document.page_content):
            metadata = document.metadata
            vendor_name = metadata.get("vendor_name", "Unknown")
            amount = _extract_cap_amount(match)
            if amount is None:
                continue
            key = (vendor_name, amount, metadata.get("filename"), metadata.get("page_number"))
            if key in seen:
                continue
            seen.add(key)
            terms.append({
                "vendor_name": vendor_name,
                "monthly_cap_inr": amount,   # kept for backward compat
                "monthly_cap_amount": amount,
                "source_file": metadata.get("filename", "Unknown"),
                "page_number": metadata.get("page_number", "?"),
                "effective_date": metadata.get("effective_date"),
                "expiry_date": metadata.get("expiry_date"),
            })
    return terms


def _has_conflicting_monthly_cap_values(contract_cap_terms: list[dict]) -> bool:
    """Return true when one vendor has multiple cited cap values.

    The current policy supports one cap across one contract term. A changed cap
    inside a month needs explicit effective-date/proration policy, so it must
    not be silently reduced to whichever clause retrieval returned first.
    """
    caps_by_vendor: dict[str, set[int]] = {}
    for term in contract_cap_terms:
        vendor_name = str(term.get("vendor_name", "Unknown"))
        cap = term.get("monthly_cap_amount") or term.get("monthly_cap_inr")
        if isinstance(cap, (int, float)):
            caps_by_vendor.setdefault(vendor_name, set()).add(int(cap))
    return any(len(caps) > 1 for caps in caps_by_vendor.values())


def _abstain_response(
    state: AgentState,
    *,
    reason: str,
    reason_code: str,
    recommended_action: str,
    confidence: float | None = None,
    confidence_threshold: float | None = None,
) -> dict:
    """Decline a legal conclusion when the retrieved evidence is unreliable."""
    updated_context = dict(state.get("context_data", {}))
    updated_context.setdefault("abstentions", []).append({
        "agent": "legal_agent",
        "reason": reason,
    })
    escalation = build_review_escalation(
        agent="legal_agent",
        reason_code=reason_code,
        reason=reason,
        recommended_action=recommended_action,
        confidence=round(confidence, 4) if confidence is not None else None,
        confidence_threshold=confidence_threshold,
    )
    return {
        "messages": [{
            "role": "legal_agent",
            "content": f"ABSTAINED: {reason} No legal conclusion was made.",
        }],
        "context_data": updated_context,
        "cited_sources": [],
        "sql_queries": [],
        "error_log": [reason],
        "review_escalations": [escalation],
        "next_node": "supervisor",
        "final_report": "",
        "iteration_count": state.get("iteration_count", 0),
    }
