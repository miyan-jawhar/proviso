"""
Agent State Definition
======================
Shared TypedDict state for the LangGraph multi-agent supervisor.
"""

import operator
from typing import TypedDict, Annotated


class AgentState(TypedDict):
    """Global state shared across all agent nodes in the graph.

    Attributes:
        messages: Accumulated chat messages (appended via operator.add)
        next_node: Target node for conditional routing
        context_data: Shared context between agents (contract terms, query results, etc.)
        final_report: Accumulated final response for the user
        cited_sources: XAI — list of source citations (filename, page, clause)
        sql_queries: XAI — list of SQL queries that were executed
        error_log: Errors encountered during processing
        iteration_count: Guard against infinite loops
    """
    messages: Annotated[list[dict], operator.add]
    next_node: str
    context_data: dict
    final_report: str
    cited_sources: Annotated[list[dict], operator.add]
    sql_queries: Annotated[list[str], operator.add]
    error_log: Annotated[list[str], operator.add]
    iteration_count: int
