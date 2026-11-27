"""
Auth Routes
=============
User registration, login (JWT), and token refresh endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas import UserRegister, UserLogin, TokenResponse, TokenRefresh
from app.core.database import get_db
from app.core.auth import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(user: UserRegister, db: AsyncSession = Depends(get_db)):
    """Register a durable local user account."""
    try:
        await db.execute(text("""
            INSERT INTO users (username, hashed_password, full_name, role, is_active)
            VALUES (:username, :hashed_password, :full_name, 'analyst', 1)
        """), {
            "username": user.username,
            "hashed_password": hash_password(user.password),
            "full_name": user.full_name or user.username,
        })
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    access = create_access_token({"sub": user.username, "role": "analyst"})
    refresh = create_refresh_token({"sub": user.username})
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    """Authenticate and receive JWT tokens."""
    user = (await db.execute(text("""
        SELECT username, hashed_password, role, is_active
        FROM users WHERE username = :username
    """), {"username": credentials.username})).mappings().first()
    if (
        not user
        or not bool(user["is_active"])
        or not verify_password(credentials.password, user["hashed_password"])
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    access = create_access_token({"sub": user["username"], "role": user["role"]})
    refresh = create_refresh_token({"sub": user["username"]})
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: TokenRefresh, db: AsyncSession = Depends(get_db)):
    """Exchange a refresh token for new access + refresh tokens."""
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    username = payload.get("sub")
    user = (await db.execute(text("""
        SELECT username, role, is_active FROM users WHERE username = :username
    """), {"username": username})).mappings().first()
    if not user or not bool(user["is_active"]):
        raise HTTPException(status_code=404, detail="User not found")

    access = create_access_token({"sub": username, "role": user["role"]})
    refresh = create_refresh_token({"sub": username})
    return TokenResponse(access_token=access, refresh_token=refresh)
