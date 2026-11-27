"""
Agent State Definition
======================
Shared TypedDict state for the LangGraph multi-agent supervisor.
"""

import operator
from typing import TypedDict, Annotated


class AgentState(TypedDict):
    """Global state shared across all agent nodes in the graph."""
    messages: Annotated[list[dict], operator.add]
    next_node: str
    context_data: dict
    final_report: str
    cited_sources: Annotated[list[dict], operator.add]
    sql_queries: Annotated[list[str], operator.add]
    error_log: Annotated[list[str], operator.add]
    review_escalations: Annotated[list[dict], operator.add]
    iteration_count: int
    risk_scorecard: dict
