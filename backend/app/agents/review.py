"""Structured human-review escalations for evidence-bound agent work."""

from __future__ import annotations

from typing import Any


def build_review_escalation(
    *,
    agent: str,
    reason_code: str,
    reason: str,
    recommended_action: str,
    confidence: float | None = None,
    confidence_threshold: float | None = None,
) -> dict[str, Any]:
    """Describe why the system declined to make a material conclusion.

    This is intentionally structured rather than encoded only in prose, so the
    API, Streamlit UI, and evaluation harness can distinguish a review handoff
    from a normal answer or an execution failure.
    """
    return {
        "status": "human_review_required",
        "agent": agent,
        "reason_code": reason_code,
        "reason": reason,
        "confidence": confidence,
        "confidence_threshold": confidence_threshold,
        "recommended_action": recommended_action,
        "blocking": True,
    }
