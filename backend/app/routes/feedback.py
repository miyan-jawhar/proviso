"""
Feedback Route
===============
RLHF-style feedback loop — users rate AI responses for future fine-tuning.
"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas import FeedbackCreate, FeedbackResponse
from app.core.auth import get_current_user
from app.core.database import get_db

router = APIRouter(tags=["Feedback"])
logger = logging.getLogger("proviso.feedback")

@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    feedback: FeedbackCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Store 1-5 feedback with the authenticated user in the local database."""
    created_at = datetime.now(timezone.utc).isoformat()
    result = await db.execute(text("""
        INSERT INTO feedback (username, query_text, response_text, rating, comment, created_at)
        VALUES (:username, :query_text, :response_text, :rating, :comment, :created_at)
        RETURNING feedback_id
    """), {
        "username": current_user["username"],
        "query_text": feedback.query_text,
        "response_text": feedback.response_text[:1000],
        "rating": feedback.rating,
        "comment": feedback.comment,
        "created_at": created_at,
    })
    feedback_id = result.scalar_one()
    await db.commit()

    logger.info(f"Feedback from {current_user['username']}: rating={feedback.rating}")

    return FeedbackResponse(
        feedback_id=feedback_id,
        message="Feedback recorded successfully. Thank you!",
    )


@router.get("/feedback")
async def list_feedback(
    limit: int = Query(default=100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return durable feedback; administrators can inspect all records."""
    if current_user.get("role") == "admin":
        statement = text("""
            SELECT feedback_id, username, query_text, response_text, rating, comment, created_at
            FROM feedback ORDER BY feedback_id DESC LIMIT :limit
        """)
        params = {"limit": limit}
    else:
        statement = text("""
            SELECT feedback_id, username, query_text, response_text, rating, comment, created_at
            FROM feedback WHERE username = :username ORDER BY feedback_id DESC LIMIT :limit
        """)
        params = {"username": current_user["username"], "limit": limit}

    records = (await db.execute(statement, params)).mappings().all()
    return {"records": [dict(record) for record in records]}
