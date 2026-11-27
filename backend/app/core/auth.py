"""
JWT Authentication
===================
JWT token creation and verification using PyJWT + pwdlib (Argon2).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.config import get_settings

# Try pwdlib first, fall back to passlib
try:
    from pwdlib import PasswordHash
    pwd_context = PasswordHash.recommended()

    def verify_password(plain: str, hashed: str) -> bool:
        return pwd_context.verify(plain, hashed)

    def hash_password(password: str) -> str:
        return pwd_context.hash(password)

except ImportError:
    # Fallback: simple hashlib-based hashing for development
    import hashlib

    def verify_password(plain: str, hashed: str) -> bool:
        return hashlib.sha256(plain.encode()).hexdigest() == hashed

    def hash_password(password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict) -> str:
    """Create a JWT refresh token."""
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """FastAPI dependency: extract and validate the current user from JWT."""
    payload = decode_token(token)
    username = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    return {"username": username, "role": payload.get("role", "analyst")}
