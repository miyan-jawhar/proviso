"""
LangGraph Orchestration
========================
Compiles the multi-agent StateGraph with supervisor routing.
Entry: supervisor → conditional edges → legal/finance agents → back to supervisor → FINISH
"""

import os
from typing import Any
from langgraph.graph import StateGraph, END

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
import chromadb

from app.agents.state import AgentState
from app.agents.supervisor import create_supervisor_node
from app.agents.legal_agent import create_legal_node
from app.agents.finance_agent import create_finance_node
from app.agents.risk_synthesizer import create_risk_synthesizer_node
from app.config import get_settings


class GeminiFailoverLLM:
    """Use a proven Gemini Flash fallback only for temporary provider failures."""

    def __init__(self, primary: Any, fallback: Any):
        self.primary = primary
        self.fallback = fallback

    def invoke(self, *args, **kwargs):
        try:
            return self.primary.invoke(*args, **kwargs)
        except Exception as exc:
            error = str(exc).upper()
            transient_failure = any(code in error for code in ("429", "500", "502", "503", "504"))
            transient_failure = transient_failure or any(
                marker in error for marker in ("UNAVAILABLE", "RESOURCE_EXHAUSTED")
            )
            if not transient_failure:
                raise
            return self.fallback.invoke(*args, **kwargs)


def _build_llm(settings=None):
    """Build the LLM based on configuration."""
    if settings is None:
        settings = get_settings()

    provider = settings.llm_provider.lower()
    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY is required when LLM_PROVIDER=gemini. "
                "Add a Gemini Developer API key to .env."
            )
        primary = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            api_key=settings.gemini_api_key,
            temperature=0,
            max_retries=0,
        )
        fallback_model = getattr(settings, "gemini_fallback_model", "")
        if fallback_model and fallback_model != settings.gemini_model:
            fallback = ChatGoogleGenerativeAI(
                model=fallback_model,
                api_key=settings.gemini_api_key,
                temperature=0,
                max_retries=0,
            )
            return GeminiFailoverLLM(primary, fallback)
        return primary

    if provider == "openai":
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=0,
        )

    if provider == "ollama":
        # Ollama via ChatOpenAI-compatible endpoint
        return ChatOpenAI(
            model=settings.ollama_model,
            base_url=f"{settings.ollama_base_url}/v1",
            api_key="ollama",
            temperature=0,
        )

    raise ValueError(
        "Unsupported LLM_PROVIDER. Choose one of: gemini, openai, ollama."
    )


def _build_vectorstore(settings=None):
    """Build the ChromaDB vector store used by the Legal Agent."""
    if settings is None:
        settings = get_settings()

    # Embedding model
    embeddings = HuggingFaceEmbeddings(
        model_name=settings.embedding_model,
        cache_folder=settings.embedding_cache_dir,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    # ChromaDB connection
    if settings.use_local_chroma:
        vectorstore = Chroma(
            collection_name=settings.chroma_collection,
            embedding_function=embeddings,
            persist_directory=settings.local_chroma_dir,
        )
    else:
        chroma_client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
        )
        vectorstore = Chroma(
            client=chroma_client,
            collection_name=settings.chroma_collection,
            embedding_function=embeddings,
        )

    return vectorstore


def build_graph(settings=None):
    """Build and compile the LangGraph multi-agent graph.

    Returns:
        Compiled LangGraph application ready for invocation.
    """
    if settings is None:
        settings = get_settings()

    # Initialize components
    llm = _build_llm(settings)
    vectorstore = _build_vectorstore(settings)

    # Create agent nodes
    supervisor = create_supervisor_node(llm)
    legal = create_legal_node(
        llm,
        vectorstore,
        min_relevance_score=settings.retrieval_min_relevance_score,
    )
    finance = create_finance_node(llm, settings.database_readonly_url)
    risk_synthesizer = create_risk_synthesizer_node(llm)

    # Build the graph
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("supervisor", supervisor)
    workflow.add_node("legal_agent", legal)
    workflow.add_node("finance_agent", finance)
    workflow.add_node("risk_synthesizer", risk_synthesizer)

    # Routing: supervisor decides next node
    def route_to_agent(state: AgentState) -> str:
        next_node = state.get("next_node", "FINISH")
        if next_node == "FINISH":
            return END
        return next_node

    workflow.add_conditional_edges(
        "supervisor",
        route_to_agent,
        {
            "legal_agent": "legal_agent",
            "finance_agent": "finance_agent",
            "risk_synthesizer": "risk_synthesizer",
            END: END,
        }
    )

    # Sub-agents always return to supervisor for next decision
    workflow.add_edge("legal_agent", "supervisor")
    workflow.add_edge("finance_agent", "supervisor")
    workflow.add_edge("risk_synthesizer", "supervisor")

    # Entry point
    workflow.set_entry_point("supervisor")

    # Compile
    graph = workflow.compile()
    return graph


def invoke_graph(graph, user_query: str) -> dict:
    """Invoke the compiled graph with a user query.

    Args:
        graph: Compiled LangGraph application
        user_query: Natural language query string

    Returns:
        dict with keys: final_report, cited_sources, sql_queries, error_log, messages
    """
    initial_state = {
        "messages": [{"role": "user", "content": user_query}],
        "next_node": "",
        "context_data": {},
        "final_report": "",
        "cited_sources": [],
        "sql_queries": [],
        "error_log": [],
        "review_escalations": [],
        "iteration_count": 0,
    }

    # Run the graph
    result = graph.invoke(initial_state)

    # Extract risk_scorecard from final context_data
    final_context = result.get("context_data", {})
    return {
        "final_report": result.get("final_report", ""),
        "cited_sources": result.get("cited_sources", []),
        "sql_queries": result.get("sql_queries", []),
        "error_log": result.get("error_log", []),
        "review_escalations": result.get("review_escalations", []),
        "messages": result.get("messages", []),
        "iterations": result.get("iteration_count", 0),
        "risk_scorecard": final_context.get("risk_scorecard"),
    }
