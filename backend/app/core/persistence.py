"""Local durable storage for Proviso identities and feedback.

The prototype deliberately keeps its default runtime local: the same
SQLite ledger used for invoices also persists application users and feedback.
The schema helper supports PostgreSQL as the optional Docker profile.
"""

from sqlalchemy import text

from app.config import get_settings
from app.core.auth import hash_password
from app.core.database import get_engine


_DEMO_ADMIN_USERNAME = "admin"
_PLACEHOLDER_ADMIN_HASH = "$argon2id$v=19$m=65536,t=3,p=4$placeholder_admin_hash"


async def ensure_persistence_schema(settings=None) -> None:
    """Create or migrate the small auth/feedback schema and seed demo admin.

    This is intentionally idempotent so local startup does not require a
    separate migration service. Production deployments should replace this
    helper with managed migrations and an external identity provider.
    """
    settings = settings or get_settings()
    engine = get_engine(settings)
    dialect = engine.dialect.name

    try:
        async with engine.begin() as connection:
            if dialect == "sqlite":
                await connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username VARCHAR(100) UNIQUE NOT NULL,
                        hashed_password VARCHAR(500) NOT NULL,
                        full_name VARCHAR(255),
                        role VARCHAR(50) NOT NULL DEFAULT 'analyst',
                        is_active INTEGER NOT NULL DEFAULT 1
                    )
                """))
                await connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS feedback (
                        feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username VARCHAR(100),
                        query_text VARCHAR(2000),
                        response_text VARCHAR(5000),
                        rating INTEGER NOT NULL,
                        comment VARCHAR(1000),
                        created_at VARCHAR(50) NOT NULL
                    )
                """))
                user_columns = {
                    row["name"]
                    for row in (await connection.execute(text("PRAGMA table_info(users)"))).mappings()
                }
                feedback_columns = {
                    row["name"]
                    for row in (await connection.execute(text("PRAGMA table_info(feedback)"))).mappings()
                }
                if "is_active" not in user_columns:
                    await connection.execute(text(
                        "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
                    ))
                if "username" not in feedback_columns:
                    await connection.execute(text(
                        "ALTER TABLE feedback ADD COLUMN username VARCHAR(100)"
                    ))
            else:
                await connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id SERIAL PRIMARY KEY,
                        username VARCHAR(100) UNIQUE NOT NULL,
                        hashed_password VARCHAR(500) NOT NULL,
                        full_name VARCHAR(255),
                        role VARCHAR(50) NOT NULL DEFAULT 'analyst',
                        is_active INTEGER NOT NULL DEFAULT 1
                    )
                """))
                await connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS feedback (
                        feedback_id SERIAL PRIMARY KEY,
                        username VARCHAR(100),
                        query_text VARCHAR(2000),
                        response_text VARCHAR(5000),
                        rating INTEGER NOT NULL,
                        comment VARCHAR(1000),
                        created_at VARCHAR(50) NOT NULL
                    )
                """))
                await connection.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active INTEGER NOT NULL DEFAULT 1"
                ))
                await connection.execute(text(
                    "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS username VARCHAR(100)"
                ))

            existing = (await connection.execute(text("""
                SELECT hashed_password FROM users WHERE username = :username
            """), {"username": _DEMO_ADMIN_USERNAME})).mappings().first()
            if existing is None:
                await connection.execute(text("""
                    INSERT INTO users (username, hashed_password, full_name, role, is_active)
                    VALUES (:username, :hashed_password, :full_name, 'admin', 1)
                """), {
                    "username": _DEMO_ADMIN_USERNAME,
                    "hashed_password": hash_password("admin123"),
                    "full_name": "System Administrator",
                })
            elif existing["hashed_password"] == _PLACEHOLDER_ADMIN_HASH:
                await connection.execute(text("""
                    UPDATE users SET hashed_password = :hashed_password, role = 'admin', is_active = 1
                    WHERE username = :username
                """), {
                    "username": _DEMO_ADMIN_USERNAME,
                    "hashed_password": hash_password("admin123"),
                })
    finally:
        await engine.dispose()
