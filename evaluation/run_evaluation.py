"""Run the versioned evaluation manifest against the authenticated API."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


EVALUATION_DIR = Path(__file__).parent
DEFAULT_CASES = EVALUATION_DIR / "cases.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        cases = json.load(handle)

    if not isinstance(cases, list) or not 20 <= len(cases) <= 60:
        raise ValueError("The evaluation manifest must contain 20 to 60 cases.")
    if len({case.get("id") for case in cases}) != len(cases):
        raise ValueError("Every evaluation case must have a unique id.")
    return cases


def _contains_all(text: str, terms: list[str]) -> bool:
    normalized = text.lower()
    return all(term.lower() in normalized for term in terms)


def evaluate_case(session: requests.Session, api_url: str, case: dict[str, Any]) -> dict[str, Any]:
    response = session.post(f"{api_url}/query", json={"query": case["query"]}, timeout=120)
    expected_status = case.get("expected_http_status", 200)
    record: dict[str, Any] = {
        "id": case["id"],
        "category": case["category"],
        "expected_status": expected_status,
        "actual_status": response.status_code,
        "checks": {"http_status": response.status_code == expected_status},
    }

    if response.status_code != 200:
        record["response"] = response.text[:1000]
        record["passed"] = record["checks"]["http_status"]
        return record

    payload = response.json()
    answer = payload.get("answer", "")
    trace_agents = [step.get("agent") for step in payload.get("agent_trace", [])]
    citations = payload.get("cited_sources", [])
    sql_queries = payload.get("sql_queries", [])
    errors = payload.get("error_log", [])
    review_escalations = payload.get("review_escalations", [])

    expected_agents = set(case.get("expected_agents", []))
    expected_vendors = set(case.get("expected_source_vendors", []))
    expected_tables = set(case.get("expected_sql_tables", []))
    expected_terms = case.get("expected_answer_terms", [])
    citation_vendors = {source.get("vendor_name") for source in citations}
    sql_text = "\n".join(sql_queries).lower()

    checks = record["checks"]
    checks["routing"] = expected_agents.issubset(set(trace_agents))
    checks["citation"] = not expected_vendors or expected_vendors.issubset(citation_vendors)
    checks["sql_trace"] = (
        not expected_tables
        or (all(table in sql_text for table in expected_tables) and bool(sql_queries) and not errors)
    )
    checks["answer_terms"] = not expected_terms or _contains_all(answer, expected_terms)
    expected_behavior = case.get("expected_behavior", "answer")
    if expected_behavior == "answer":
        checks["no_review_escalation"] = not review_escalations
    elif expected_behavior == "human_review":
        expected_reason_codes = set(case.get("expected_review_reason_codes", []))
        returned_reason_codes = {item.get("reason_code") for item in review_escalations}
        checks["review_escalation"] = (
            bool(review_escalations)
            and expected_reason_codes.issubset(returned_reason_codes)
            and "human review required" in answer.lower()
        )
    record["passed"] = all(checks.values())
    record["trace_agents"] = trace_agents
    record["citation_vendors"] = sorted(vendor for vendor in citation_vendors if vendor)
    record["sql_queries"] = sql_queries
    record["error_log"] = errors
    record["review_escalations"] = review_escalations
    record["answer_preview"] = answer[:1000]
    return record


def percentage(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 1) if denominator else None


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = {
        name: [record for record in records if name in record["checks"]]
        for name in ("routing", "citation", "sql_trace", "answer_terms", "http_status")
    }
    uncertainty_records = [
        record for record in records if record["category"] == "adversarial_uncertainty"
    ]
    clear_reference_records = [
        record for record in records
        if record["category"] not in {"adversarial_uncertainty", "adversarial_security"}
    ]
    return {
        "case_pass_rate": percentage(sum(record["passed"] for record in records), len(records)),
        "routing_accuracy": percentage(
            sum(record["checks"].get("routing", False) for record in applicable["routing"]),
            len(applicable["routing"]),
        ),
        "citation_recall_at_k": percentage(
            sum(record["checks"].get("citation", False) for record in applicable["citation"]),
            len(applicable["citation"]),
        ),
        "sql_trace_coverage": percentage(
            sum(record["checks"].get("sql_trace", False) for record in applicable["sql_trace"]),
            len(applicable["sql_trace"]),
        ),
        "answer_assertion_coverage": percentage(
            sum(record["checks"].get("answer_terms", False) for record in applicable["answer_terms"]),
            len(applicable["answer_terms"]),
        ),
        "safety_rejection_rate": percentage(
            sum(
                record["checks"].get("http_status", False)
                for record in records
                if record["category"] == "adversarial_security"
            ),
            sum(record["category"] == "adversarial_security" for record in records),
        ),
        "human_review_handoff_rate": percentage(
            sum(
                record["checks"].get("review_escalation", False)
                for record in uncertainty_records
            ),
            len(uncertainty_records),
        ),
        "unsafe_uncertainty_conclusion_rate": percentage(
            sum(not record.get("review_escalations") for record in uncertainty_records),
            len(uncertainty_records),
        ),
        "clear_case_false_abstention_rate": percentage(
            sum(bool(record.get("review_escalations")) for record in clear_reference_records),
            len(clear_reference_records),
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--api-url", default=os.getenv("BACKEND_API_URL", "http://localhost:8000"))
    parser.add_argument("--username", default=os.getenv("EVAL_USERNAME", "admin"))
    parser.add_argument("--password", default=os.getenv("EVAL_PASSWORD", "admin123"))
    parser.add_argument(
        "--category",
        action="append",
        help="Evaluate only one or more categories; repeat the option for multiple categories.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=2.1,
        help="Delay between API cases to stay below the default 30 requests/minute limit.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.delay_seconds < 0:
        raise ValueError("--delay-seconds must be zero or positive.")

    cases = load_cases(args.cases)
    if args.category:
        requested_categories = set(args.category)
        cases = [case for case in cases if case["category"] in requested_categories]
        if not cases:
            raise ValueError("No evaluation cases matched the requested --category values.")
    category_counts = Counter(case["category"] for case in cases)
    if args.dry_run:
        print(json.dumps({"case_count": len(cases), "categories": category_counts}, indent=2))
        return

    session = requests.Session()
    login = session.post(
        f"{args.api_url}/auth/login",
        json={"username": args.username, "password": args.password},
        timeout=15,
    )
    login.raise_for_status()
    session.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

    records = []
    for index, case in enumerate(cases):
        records.append(evaluate_case(session, args.api_url, case))
        if args.delay_seconds and index < len(cases) - 1:
            time.sleep(args.delay_seconds)
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "api_url": args.api_url,
        "manifest": str(args.cases),
        "request_delay_seconds": args.delay_seconds,
        "summary": summarize(records),
        "records": records,
    }
    results_dir = EVALUATION_DIR / "results"
    results_dir.mkdir(exist_ok=True)
    output = results_dir / f"evaluation-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "result_file": str(output)}, indent=2))


if __name__ == "__main__":
    main()
