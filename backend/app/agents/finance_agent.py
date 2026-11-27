"""
Finance Agent (Text-to-SQL)
============================
Financial analyst agent with read-only SQL access to PostgreSQL.
Converts natural language to SQL, executes safely, and returns results.
Features: SQL guardrails, self-correction loop, query logging for XAI.
"""

import ast
import json
import re
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_community.utilities import SQLDatabase
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from app.agents.state import AgentState
from app.agents.review import build_review_escalation
from app.core.security import SQLValidationError, validate_sql_query
from app.agents.llm_utils import response_text


# Blocked SQL keywords (case-insensitive)
BLOCKED_KEYWORDS = re.compile(
    r'\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|EXEC|EXECUTE)\b',
    re.IGNORECASE
)

FINANCE_SYSTEM_PROMPT = """You are an Enterprise Financial Analyst AI.
You have read-only access to a vendor invoice and payment database.

DATABASE SCHEMA:
================
Table: vendors
  - vendor_id (INTEGER, PRIMARY KEY)
  - vendor_name (VARCHAR)
  - country (VARCHAR)
  - risk_tier (VARCHAR) — values: LOW, MEDIUM, HIGH, CRITICAL

Table: invoices
  - invoice_id (INTEGER, PRIMARY KEY)
  - vendor_id (INTEGER, FOREIGN KEY -> vendors.vendor_id)
  - invoice_date (DATE)
  - amount_due (FLOAT)
  - payment_status (VARCHAR) — values: PAID, PENDING, OVERDUE

IMPORTANT RULES:
1. ONLY generate SELECT queries. Never generate DROP, DELETE, UPDATE, INSERT, or any DDL.
2. Always use JOINs when the query involves vendor names (the invoices table only has vendor_id).
3. Format monetary amounts clearly (e.g., "INR 5,00,000").
4. When asked about overpayments, compare actual amounts against any contract cap provided in context.
5. Be precise with date filters — the data spans the last 24 months.

RESPONSE FORMAT:
Provide your response as:
SQL_QUERY: [The exact SQL query you want to execute]
ANALYSIS: [Your interpretation of the results]
"""

MAX_SQL_RETRIES = 3

MONTHLY_CAP_POLICY = """

MONTHLY CAP-COMPLIANCE POLICY:
- A contract monthly cap applies to the SUM of one vendor's invoices in each calendar month.
- For a cap-compliance query, NEVER use AVG(amount_due), a single average invoice,
  or a whole-period total. Group by vendor and calendar month and return the
  month, monthly sum, supplied cap, and excess.
- Use only CONTRACT CAP EVIDENCE supplied from cited legal retrieval. If it is
  absent or ambiguous, do not invent a cap; the task must be escalated.
- Include only invoices in the supplied contract effective term and use the
  database dialect for calendar-month grouping.
- Do not prorate or choose between multiple cap values. If the cited evidence
  contains an effective-dated cap change, escalate for an approved schedule and
  month-splitting policy.
"""


def create_finance_node(llm, db_url: str):
    """Create the Finance Agent node with SQL database access."""

    # PostgreSQL uses the dedicated readonly_agent role. SQLite is used for
    # local development, so every connection is additionally put in query-only
    # mode before SQLDatabase can execute agent-generated statements.
    engine_options = {"pool_pre_ping": True}
    # A local SQLite file is embedded in-process; do not leave pooled DB-API
    # connections around between agent calls. PostgreSQL retains normal pooling.
    if db_url.startswith("sqlite"):
        engine_options["poolclass"] = NullPool
    engine = create_engine(db_url, **engine_options)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _set_sqlite_query_only(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA query_only = ON")
            cursor.close()

    db = SQLDatabase(engine=engine)

    def finance_node(state: AgentState) -> dict:
        """Finance Agent: converts NL to SQL, executes, analyzes results."""
        messages = state.get("messages", [])
        context = state.get("context_data", {})

        # Extract query
        query = _extract_query(messages)
        if not query:
            return _empty_response("No query found for financial analysis.")

        # Build prompt with legal findings and cited structured cap evidence.
        context_sections = []
        if "legal_findings" in context:
            context_sections.append(f"CONTEXT FROM LEGAL REVIEW:\n{context['legal_findings']}")
        contract_cap_terms = _select_query_cap_terms(
            context.get("contract_cap_terms", []), query
        )
        cap_question = _is_monthly_cap_question(query)
        if cap_question and not contract_cap_terms:
            return _review_response(
                state,
                reason=(
                    "No cited monthly contract-cap evidence was available for a cap-compliance "
                    "calculation, so the finance agent cannot select an authoritative threshold."
                ),
                attempted_queries=[],
                reason_code="missing_contract_cap_evidence",
                recommended_action=(
                    "Retrieve the executed contract's monthly cap and effective term before "
                    "comparing ledger activity."
                ),
            )
        if cap_question and _has_conflicting_monthly_cap_values(contract_cap_terms):
            return _review_response(
                state,
                reason=(
                    "Cited legal evidence contains multiple monthly cap values for the same "
                    "vendor. The deterministic finance policy cannot select one or prorate a "
                    "month without an authoritative effective-dated schedule."
                ),
                attempted_queries=[],
                reason_code="ambiguous_effective_dated_cap_terms",
                recommended_action=(
                    "Provide the executed amendment or an approved effective-dated cap schedule, "
                    "including the policy for a month in which the cap changes."
                ),
            )
        if contract_cap_terms:
            context_sections.append(
                "CONTRACT CAP EVIDENCE (cited contract terms):\n"
                f"{json.dumps(contract_cap_terms, indent=2)}"
            )
        context_info = "\n\n" + "\n\n".join(context_sections) if context_sections else ""

        # A cited monthly cap is a fixed policy computation, not an open-ended
        # text-to-SQL task. Build its SELECT statement deterministically from
        # the legal evidence and the live database dialect. This makes the
        # high-stakes result reproducible and prevents a model from switching
        # between PostgreSQL and SQLite date syntax.
        if cap_question:
            return _monthly_cap_response(
                state=state,
                context=context,
                db=db,
                dialect=db.dialect,
                contract_cap_terms=contract_cap_terms,
            )

        # Standard audit metrics are deterministic read-only calculations.
        # Keeping these templates local avoids turning simple, auditable ledger
        # questions into an availability dependency on a free-tier model. More
        # open-ended financial questions still use the LLM flow below.
        standard_metric = _build_standard_metric_query(query, db.dialect)
        if standard_metric:
            return _standard_metric_response(
                state=state,
                context=context,
                db=db,
                sql_query=standard_metric["sql_query"],
                vendor_name=standard_metric["vendor_name"],
                finding_label=standard_metric["finding_label"],
            )

        # Get database schema info
        try:
            table_info = db.get_table_info()
        except Exception:
            table_info = "(Schema info unavailable)"

        # Generate SQL with self-correction loop
        sql_query = None
        results = None
        executed_queries = []
        last_error = None

        for attempt in range(MAX_SQL_RETRIES):
            # Build messages for SQL generation
            gen_messages = [
                SystemMessage(content=FINANCE_SYSTEM_PROMPT + MONTHLY_CAP_POLICY),
                HumanMessage(content=(
                    f"USER QUERY: {query}{context_info}\n\n"
                    f"DATABASE DIALECT: {db.dialect}\n"
                    "Use syntax that is valid for this exact dialect.\n\n"
                    f"LIVE TABLE INFO:\n{table_info}\n\n"
                    f"Generate a SELECT query to answer this question."
                )),
            ]

            # If previous attempt failed, include the error
            if last_error:
                gen_messages.append(HumanMessage(content=(
                    f"The previous SQL query failed with this error:\n"
                    f"Query: {sql_query}\n"
                    f"Error: {last_error}\n\n"
                    f"Please fix the query and try again."
                )))

            try:
                response = llm.invoke(gen_messages)
                content = response_text(response)

                # Extract SQL from response
                sql_query = _extract_sql(content)
                if not sql_query:
                    last_error = "Could not extract a valid SQL query from LLM response."
                    continue

                if cap_question and not _is_monthly_cap_sql(sql_query, contract_cap_terms):
                    last_error = (
                        "A monthly-cap calculation must group SUM(amount_due) by calendar month, "
                        "use a cited cap value, and must not use AVG(amount_due)."
                    )
                    executed_queries.append(f"/* REJECTED: MONTHLY CAP POLICY */ {sql_query}")
                    continue

                # Guardrail: block destructive SQL
                if BLOCKED_KEYWORDS.search(sql_query):
                    return _empty_response(
                        "SECURITY VIOLATION: Attempted to execute a destructive SQL command. "
                        "Only SELECT queries are permitted."
                    )

                try:
                    validate_sql_query(sql_query, dialect=db.dialect)
                except SQLValidationError as exc:
                    return _empty_response(
                        f"SECURITY VIOLATION: Generated SQL was rejected: {exc}"
                    )

                # Execute query
                results = db.run(sql_query)
                executed_queries.append(sql_query)
                last_error = None
                break  # Success

            except Exception as e:
                last_error = str(e)
                if sql_query:
                    executed_queries.append(f"/* FAILED */ {sql_query}")
                continue

        # Generate analysis of results
        if results is not None and last_error is None:
            analysis_messages = [
                SystemMessage(content=(
                    "You are a financial analyst. Interpret the following SQL query results "
                    "and provide a clear, business-friendly analysis. Highlight any anomalies."
                )),
                HumanMessage(content=(
                    f"ORIGINAL QUESTION: {query}\n\n"
                    f"SQL QUERY:\n{sql_query}\n\n"
                    f"RESULTS:\n{results}\n\n"
                    f"Provide your analysis."
                )),
            ]
            try:
                analysis_response = llm.invoke(analysis_messages)
                analysis = response_text(analysis_response)
            except Exception as e:
                analysis = f"Query results:\n{results}\n\n(Analysis generation failed: {e})"
        else:
            return _review_response(
                state,
                reason=(
                    f"Unable to execute a read-only financial query after {MAX_SQL_RETRIES} attempts. "
                    f"Last error: {last_error}"
                ),
                attempted_queries=executed_queries,
            )

        # Update context
        updated_context = dict(context)
        updated_context["finance_findings"] = analysis
        updated_context["sql_results"] = str(results)[:2000] if results else None

        return {
            "messages": [{"role": "finance_agent", "content": analysis}],
            "context_data": updated_context,
            "cited_sources": [],
            "sql_queries": executed_queries,
            "error_log": [last_error] if last_error else [],
            "review_escalations": [],
            "next_node": "supervisor",
            "final_report": "",
            "iteration_count": state.get("iteration_count", 0),
        }

    return finance_node


def _monthly_cap_response(
    *,
    state: AgentState,
    context: dict,
    db: SQLDatabase,
    dialect: str,
    contract_cap_terms: list[dict],
) -> dict:
    """Execute a cited contract-cap calculation without model-generated SQL."""
    try:
        sql_query, cap_term = _build_monthly_cap_sql(contract_cap_terms, dialect)
        validate_sql_query(sql_query, dialect=dialect)
        results = db.run(sql_query)
    except (SQLValidationError, ValueError, Exception) as exc:
        return _review_response(
            state,
            reason=(
                "Unable to execute the deterministic read-only monthly-cap calculation. "
                f"Error: {exc}"
            ),
            attempted_queries=[],
            reason_code="monthly_cap_calculation_unavailable",
            recommended_action=(
                "Have a financial reviewer validate the contract term, vendor identity, "
                "and local ledger availability before making a payment conclusion."
            ),
        )

    analysis = _format_monthly_cap_analysis(results, cap_term)
    updated_context = dict(context)
    updated_context["finance_findings"] = analysis
    updated_context["sql_results"] = str(results)[:2000]

    return {
        "messages": [{"role": "finance_agent", "content": analysis}],
        "context_data": updated_context,
        "cited_sources": [],
        "sql_queries": [sql_query],
        "error_log": [],
        "review_escalations": [],
        "next_node": "supervisor",
        "final_report": "",
        "iteration_count": state.get("iteration_count", 0),
    }


def _build_standard_metric_query(query: str, dialect: str) -> dict | None:
    """Build allow-listed SQL for common one-vendor ledger audit questions."""
    normalized = query.lower()
    vendor_name = None
    sql_query = None
    finding_label = None

    spend_match = re.search(
        r"\b(?:invoice|spend(?:\s+with)?)\s+(?:for\s+)?(.+?)\s+in\s+(\d{4})\b",
        query,
        re.IGNORECASE,
    )
    if ("how much" in normalized or "total" in normalized) and spend_match:
        vendor_name, year = spend_match.groups()
        year_predicate = (
            f"CAST(strftime('%Y', i.invoice_date) AS INTEGER) = {int(year)}"
            if dialect == "sqlite"
            else f"EXTRACT(YEAR FROM i.invoice_date) = {int(year)}"
        )
        finding_label = f"Total invoiced amount in {year}"
        sql_query = (
            "SELECT v.vendor_name, SUM(i.amount_due) AS total_invoiced "
            "FROM invoices AS i JOIN vendors AS v ON v.vendor_id = i.vendor_id "
            f"WHERE v.vendor_name = {{vendor_literal}} AND {year_predicate} "
            "GROUP BY v.vendor_name"
        )

    overdue_count_match = re.search(
        r"\boverdue\s+invoices?.*?\b(?:belong\s+to|for)\s+(.+?)[?.]?$",
        query,
        re.IGNORECASE,
    )
    if sql_query is None and overdue_count_match and "how many" in normalized:
        vendor_name = overdue_count_match.group(1)
        finding_label = "Overdue invoice count"
        sql_query = (
            "SELECT v.vendor_name, COUNT(*) AS overdue_invoice_count "
            "FROM invoices AS i JOIN vendors AS v ON v.vendor_id = i.vendor_id "
            "WHERE v.vendor_name = {vendor_literal} AND i.payment_status = 'OVERDUE' "
            "GROUP BY v.vendor_name"
        )

    overdue_amount_match = re.search(
        r"\btotal\s+overdue\s+amount\s+for\s+(.+?)[?.]?$",
        query,
        re.IGNORECASE,
    )
    if sql_query is None and overdue_amount_match:
        vendor_name = overdue_amount_match.group(1)
        finding_label = "Total overdue amount"
        sql_query = (
            "SELECT v.vendor_name, SUM(i.amount_due) AS total_overdue_amount "
            "FROM invoices AS i JOIN vendors AS v ON v.vendor_id = i.vendor_id "
            "WHERE v.vendor_name = {vendor_literal} AND i.payment_status = 'OVERDUE' "
            "GROUP BY v.vendor_name"
        )

    duplicate_match = re.search(
        r"\bduplicate(?:\s+invoice)?\s+payments?\s+for\s+(.+?)[?.]?$",
        query,
        re.IGNORECASE,
    )
    if sql_query is None and duplicate_match:
        vendor_name = duplicate_match.group(1)
        finding_label = "Potential duplicate invoice payments"
        sql_query = (
            "SELECT v.vendor_name, i.invoice_date, i.amount_due, COUNT(*) AS duplicate_count "
            "FROM invoices AS i JOIN vendors AS v ON v.vendor_id = i.vendor_id "
            "WHERE v.vendor_name = {vendor_literal} "
            "GROUP BY v.vendor_name, i.invoice_date, i.amount_due HAVING COUNT(*) > 1 "
            "ORDER BY duplicate_count DESC"
        )

    average_match = re.search(
        r"\baverage\s+invoice\s+amount\s+for\s+(.+?)[?.]?$",
        query,
        re.IGNORECASE,
    )
    if sql_query is None and average_match:
        vendor_name = average_match.group(1)
        finding_label = "Average invoice amount"
        sql_query = (
            "SELECT v.vendor_name, AVG(i.amount_due) AS average_invoice_amount "
            "FROM invoices AS i JOIN vendors AS v ON v.vendor_id = i.vendor_id "
            "WHERE v.vendor_name = {vendor_literal} GROUP BY v.vendor_name"
        )

    vendor_name = _normalise_vendor_literal(vendor_name)
    if not vendor_name or not sql_query:
        return None
    return {
        "vendor_name": vendor_name,
        "finding_label": finding_label,
        "sql_query": sql_query.format(vendor_literal=_sql_string_literal(vendor_name)),
    }


def _normalise_vendor_literal(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip(" .?\t\n")
    # Templates only use a literal vendor name when it contains ordinary
    # display-name characters. Everything else falls back to the guarded LLM.
    if not re.fullmatch(r"[A-Za-z0-9,&.' -]+", candidate):
        return None
    return candidate


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _standard_metric_response(
    *,
    state: AgentState,
    context: dict,
    db: SQLDatabase,
    sql_query: str,
    vendor_name: str,
    finding_label: str,
) -> dict:
    """Execute and present a deterministic standard ledger calculation."""
    try:
        validate_sql_query(sql_query, dialect=db.dialect)
        results = db.run(sql_query)
    except (SQLValidationError, Exception) as exc:
        return _review_response(
            state,
            reason=f"Unable to execute the deterministic read-only financial calculation. Error: {exc}",
            attempted_queries=[],
            reason_code="standard_finance_calculation_unavailable",
            recommended_action=(
                "Have a financial reviewer confirm the vendor identity and local ledger availability "
                "before making a payment conclusion."
            ),
        )

    analysis = (
        "FINDINGS:\n"
        f"- Vendor: {vendor_name}\n"
        f"- {finding_label}: {results}\n"
        "- This value was calculated with the read-only SQL shown below.\n"
        "RISK_LEVEL: REVIEW"
    )
    updated_context = dict(context)
    updated_context["finance_findings"] = analysis
    updated_context["sql_results"] = str(results)[:2000]
    return {
        "messages": [{"role": "finance_agent", "content": analysis}],
        "context_data": updated_context,
        "cited_sources": [],
        "sql_queries": [sql_query],
        "error_log": [],
        "review_escalations": [],
        "next_node": "supervisor",
        "final_report": "",
        "iteration_count": state.get("iteration_count", 0),
    }


def _build_monthly_cap_sql(contract_cap_terms: list[dict], dialect: str) -> tuple[str, dict]:
    unique_terms = {
        (
            term.get("vendor_name"),
            term.get("monthly_cap_inr"),
            term.get("effective_date"),
            term.get("expiry_date"),
        )
        for term in contract_cap_terms
    }
    if len(unique_terms) != 1:
        raise ValueError("Monthly cap evidence is ambiguous across contract terms.")

    vendor_name, monthly_cap, effective_date, expiry_date = unique_terms.pop()
    if not isinstance(vendor_name, str) or not isinstance(monthly_cap, (int, float)):
        raise ValueError("Monthly cap evidence lacks a vendor name or numeric cap.")
    if not _is_iso_date(effective_date) or not _is_iso_date(expiry_date):
        raise ValueError("Monthly cap evidence lacks valid effective and expiry dates.")

    cap = int(monthly_cap)
    if dialect == "sqlite":
        month_expression = "strftime('%Y-%m', i.invoice_date)"
    elif dialect in {"postgresql", "postgres"}:
        month_expression = "TO_CHAR(DATE_TRUNC('month', i.invoice_date), 'YYYY-MM')"
    else:
        raise ValueError(f"Unsupported database dialect for monthly-cap calculation: {dialect}")

    vendor_sql = vendor_name.replace("'", "''")
    return (
        f"SELECT v.vendor_name, {month_expression} AS invoice_month, "
        f"SUM(i.amount_due) AS monthly_invoiced, {cap} AS monthly_cap, "
        f"SUM(i.amount_due) - {cap} AS excess "
        "FROM invoices AS i "
        "JOIN vendors AS v ON v.vendor_id = i.vendor_id "
        f"WHERE v.vendor_name = '{vendor_sql}' "
        f"AND i.invoice_date BETWEEN '{effective_date}' AND '{expiry_date}' "
        f"GROUP BY v.vendor_name, {month_expression} "
        f"HAVING SUM(i.amount_due) > {cap} "
        "ORDER BY invoice_month",
        {
            "vendor_name": vendor_name,
            "monthly_cap_inr": cap,
            "effective_date": effective_date,
            "expiry_date": expiry_date,
        },
    )


def _format_monthly_cap_analysis(results, cap_term: dict) -> str:
    """Return an auditable conclusion from the deterministic SQL result set."""
    try:
        rows = ast.literal_eval(results) if isinstance(results, str) else results
    except (SyntaxError, ValueError):
        rows = []
    breach_count = len(rows) if isinstance(rows, list) else 0
    vendor_name = cap_term["vendor_name"]
    cap = cap_term["monthly_cap_inr"]

    if breach_count:
        conclusion = (
            f"BREACH: {breach_count} calendar month(s) exceeded the cited monthly cap."
        )
        risk_level = "HIGH"
    else:
        conclusion = "NO BREACH FOUND: no calendar-month aggregate exceeded the cited cap."
        risk_level = "LOW"

    return (
        "FINDINGS:\n"
        f"- Vendor: {vendor_name}\n"
        f"- Cited monthly cap: INR {cap:,}\n"
        f"- Contract term evaluated: {cap_term['effective_date']} to {cap_term['expiry_date']}\n"
        f"- {conclusion}\n"
        f"- Read-only monthly aggregation result: {results}\n"
        f"RISK_LEVEL: {risk_level}"
    )


def _is_iso_date(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def _extract_sql(text: str) -> str | None:
    """Extract SQL query from LLM response."""
    # Try to find SQL in code blocks
    code_block = re.search(r'```(?:sql)?\s*\n?(.*?)\n?```', text, re.DOTALL | re.IGNORECASE)
    if code_block:
        return code_block.group(1).strip()

    # Try SQL_QUERY: prefix
    sql_match = re.search(r'SQL_QUERY:\s*(.+?)(?:\n\n|ANALYSIS:|$)', text, re.DOTALL)
    if sql_match:
        return sql_match.group(1).strip()

    # Try to find any SELECT statement
    select_match = re.search(r'(SELECT\s+.+?)(?:;|\n\n|$)', text, re.DOTALL | re.IGNORECASE)
    if select_match:
        return select_match.group(1).strip()

    return None


def _extract_query(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg["role"] == "user":
            return msg["content"]
    if messages:
        return messages[-1].get("content", "")
    return ""


def _is_monthly_cap_question(query: str) -> bool:
    """Identify questions that need the monthly contract-cap calculation policy."""
    return bool(re.search(
        r"\b(monthly\s+)?cap\b|\boverpay(?:ment|ing)?\b|\bbilling\s+limit\b|\bmaximum\s+allowed\b",
        query,
        re.IGNORECASE,
    ))


def _select_query_cap_terms(contract_cap_terms: list[dict], query: str) -> list[dict]:
    """Avoid applying a cap retrieved for a different vendor to this query."""
    if not contract_cap_terms:
        return []
    normalized_query = query.lower()
    matching_terms = [
        term
        for term in contract_cap_terms
        if term.get("vendor_name", "").lower() in normalized_query
    ]
    if matching_terms:
        return matching_terms
    vendor_names = {term.get("vendor_name") for term in contract_cap_terms}
    return contract_cap_terms if len(vendor_names) == 1 else []


def _has_conflicting_monthly_cap_values(contract_cap_terms: list[dict]) -> bool:
    """Guard against silently choosing one value from an amended cap schedule."""
    caps_by_vendor: dict[str, set[int]] = {}
    for term in contract_cap_terms:
        vendor_name = str(term.get("vendor_name", "Unknown"))
        cap = term.get("monthly_cap_inr")
        if isinstance(cap, (int, float)):
            caps_by_vendor.setdefault(vendor_name, set()).add(int(cap))
    return any(len(caps) > 1 for caps in caps_by_vendor.values())


def _is_monthly_cap_sql(sql_query: str, contract_cap_terms: list[dict]) -> bool:
    """Enforce the minimum shape of a cap-compliance calculation before execution."""
    normalized = sql_query.lower()
    compact_sql = re.sub(r"[\s,]", "", normalized)
    has_month_grouping = bool(re.search(
        r"strftime|date_trunc|to_char|extract|substr\s*\(\s*.*invoice_date",
        normalized,
    ))
    cap_literals = {
        str(int(term["monthly_cap_inr"]))
        for term in contract_cap_terms
        if term.get("monthly_cap_inr") is not None
    }
    contract_terms = {
        (str(term.get("effective_date")), str(term.get("expiry_date")))
        for term in contract_cap_terms
        if term.get("effective_date") and term.get("expiry_date")
    }
    has_contract_term_filter = not contract_terms or any(
        effective_date in normalized and expiry_date in normalized
        for effective_date, expiry_date in contract_terms
    )
    return (
        "avg(" not in normalized
        and "sum(" in normalized
        and "group by" in normalized
        and has_month_grouping
        and any(cap in compact_sql for cap in cap_literals)
        and has_contract_term_filter
    )


def _empty_response(error_msg: str) -> dict:
    return {
        "messages": [{"role": "finance_agent", "content": error_msg}],
        "context_data": {},
        "cited_sources": [],
        "sql_queries": [],
        "error_log": [error_msg],
        "review_escalations": [],
        "next_node": "supervisor",
        "final_report": "",
        "iteration_count": 0,
    }


def _review_response(
    state: AgentState,
    *,
    reason: str,
    attempted_queries: list[str],
    reason_code: str = "finance_query_inconclusive",
    recommended_action: str | None = None,
) -> dict:
    """Escalate an inconclusive finance task rather than inventing a result."""
    updated_context = dict(state.get("context_data", {}))
    updated_context.setdefault("abstentions", []).append({
        "agent": "finance_agent",
        "reason": reason,
    })
    escalation = build_review_escalation(
        agent="finance_agent",
        reason_code=reason_code,
        reason=reason,
        recommended_action=recommended_action or (
            "Have a financial reviewer validate the intended ledger fields, date range, "
            "and generated SQL before making a payment conclusion."
        ),
    )
    return {
        "messages": [{"role": "finance_agent", "content": f"ABSTAINED: {reason}"}],
        "context_data": updated_context,
        "cited_sources": [],
        "sql_queries": attempted_queries,
        "error_log": [reason],
        "review_escalations": [escalation],
        "next_node": "supervisor",
        "final_report": "",
        "iteration_count": state.get("iteration_count", 0),
    }
