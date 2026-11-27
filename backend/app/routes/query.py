"""
Query Route
=============
Main agent endpoint — accepts natural language, invokes LangGraph, returns structured response.
"""

import time
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from app.schemas import QueryRequest, QueryResponse, ReviewEscalation, SourceCitation
from app.core.auth import get_current_user
from app.core.security import validate_user_query
from app.core.rate_limit import limiter
from app.config import get_settings
from app.agents.graph import build_graph, invoke_graph

router = APIRouter(tags=["Query"])
logger = logging.getLogger("proviso.query")

# Lazy-init graph (built once on first request)
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        logger.info("Building LangGraph agent graph (first request)...")
        _graph = build_graph()
        logger.info("Graph ready.")
    return _graph


@router.post("/query", response_model=QueryResponse)
@limiter.limit("30/minute")
async def query_agents(
    request: Request,
    query_request: QueryRequest,
    current_user: dict = Depends(get_current_user),
):
    """Submit a natural language query to the multi-agent system.

    The supervisor routes the query to Legal Agent (contracts/RAG)
    and/or Finance Agent (SQL/invoices) as needed.
    """
    # Validate & sanitize input
    clean_query = validate_user_query(query_request.query)

    logger.info(f"Query from {current_user['username']}: {clean_query[:100]}...")
    start = time.time()

    try:
        graph = _get_graph()
        result = invoke_graph(graph, clean_query)
    except Exception as e:
        logger.exception("Agent graph invocation failed")
        raise HTTPException(status_code=500, detail=f"Agent processing failed: {str(e)}")

    elapsed = round(time.time() - start, 2)
    logger.info(f"Query completed in {elapsed}s, iterations={result.get('iterations', 0)}")

    # Build response
    sources = []
    for s in result.get("cited_sources", []):
        sources.append(SourceCitation(
            filename=s.get("filename", ""),
            page_number=s.get("page_number", ""),
            vendor_name=s.get("vendor_name", ""),
            clause=s.get("clause", ""),
            relevance_rank=s.get("relevance_rank", 0),
            relevance_score=s.get("relevance_score"),
        ))

    # Build agent trace for XAI
    trace = []
    for msg in result.get("messages", []):
        if msg.get("role") in ("supervisor", "legal_agent", "finance_agent", "risk_synthesizer"):
            trace.append({
                "agent": msg["role"],
                "action": msg.get("content", "")[:300],
            })

    return QueryResponse(
        answer=result.get("final_report", "No response generated."),
        cited_sources=sources,
        sql_queries=result.get("sql_queries", []),
        agent_trace=trace,
        iterations=result.get("iterations", 0),
        error_log=result.get("error_log", []),
        review_escalations=[
            ReviewEscalation(**escalation)
            for escalation in result.get("review_escalations", [])
        ],
        risk_scorecard=result.get("risk_scorecard"),
    )
