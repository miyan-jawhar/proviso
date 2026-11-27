"""
Security Guardrails
====================
SQL injection prevention, input sanitization, query validation.
"""

import re
from fastapi import HTTPException, status
from sqlglot import exp, parse
from sqlglot.errors import ParseError

# Destructive SQL keywords that should never be in agent-generated queries
BLOCKED_SQL_PATTERN = re.compile(
    r'\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|'
    r'EXEC|EXECUTE|MERGE|REPLACE|CALL|SHUTDOWN|KILL)\b',
    re.IGNORECASE
)

# Maximum query length (prevent abuse)
MAX_QUERY_LENGTH = 2000
ALLOWED_SQL_TABLES = frozenset({"vendors", "invoices"})


class SQLValidationError(ValueError):
    """Raised when generated SQL is outside the finance agent's read-only scope."""


_ALLOWED_QUERY_ROOTS = (exp.Select, exp.Union)
_FORBIDDEN_SQL_NODES = (
    exp.Alter,
    exp.Attach,
    exp.Command,
    exp.Copy,
    exp.Create,
    exp.Delete,
    exp.Drop,
    exp.Grant,
    exp.Insert,
    exp.Into,
    exp.Merge,
    exp.Pragma,
    exp.Revoke,
    exp.TruncateTable,
    exp.Update,
    exp.Use,
)

# Suspicious patterns in user input
SUSPICIOUS_PATTERNS = [
    re.compile(r';\s*(DROP|DELETE|UPDATE|INSERT|ALTER)', re.IGNORECASE),
    re.compile(r'--.*$', re.MULTILINE),
    re.compile(r'/\*.*?\*/', re.DOTALL),
    re.compile(r'\bUNION\s+SELECT\b', re.IGNORECASE),
]

# Natural-language questions can legitimately contain words such as "delete"
# (for example, a contract's data-retention clause).  Block only patterns that
# are recognizably executable destructive SQL commands.
DESTRUCTIVE_USER_SQL_PATTERN = re.compile(
    r'\b(?:'
    r'DROP\s+(?:TABLE|DATABASE|SCHEMA|ROLE|USER)|'
    r'DELETE\s+FROM|'
    r'UPDATE\s+\S+\s+SET|'
    r'INSERT\s+INTO|'
    r'ALTER\s+(?:TABLE|DATABASE|SCHEMA)|'
    r'TRUNCATE\s+(?:TABLE\s+)?\S+|'
    r'CREATE\s+(?:TABLE|DATABASE|SCHEMA)|'
    r'(?:GRANT|REVOKE)\s+'
    r')\b',
    re.IGNORECASE,
)


def validate_user_query(query: str) -> str:
    """Validate and sanitize user input query.

    Raises HTTPException if query is suspicious or too long.
    Returns sanitized query string.
    """
    if not query or not query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query cannot be empty",
        )

    query = query.strip()

    if len(query) > MAX_QUERY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Query too long. Maximum {MAX_QUERY_LENGTH} characters.",
        )

    if DESTRUCTIVE_USER_SQL_PATTERN.search(query):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query contains a destructive SQL command and has been blocked.",
        )

    # Check for SQL injection patterns in user input
    for pattern in SUSPICIOUS_PATTERNS:
        if pattern.search(query):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Query contains suspicious patterns and has been blocked.",
            )

    return query


def validate_sql_query(sql: str, dialect: str = "sqlite") -> bool:
    """Allow one read-only query over the finance schema, or reject it.

    String matching remains a cheap defence-in-depth check, but the execution
    decision comes from a parsed SQL AST.  This blocks multi-statement input,
    DDL/DML, ``SELECT INTO``, engine commands, and access to tables outside the
    allow-list.
    """
    if not sql or not sql.strip():
        raise SQLValidationError("Generated SQL cannot be empty.")

    if BLOCKED_SQL_PATTERN.search(sql):
        raise SQLValidationError("Destructive SQL keywords are not permitted.")

    try:
        statements = parse(sql, read=dialect)
    except ParseError as exc:
        raise SQLValidationError(f"SQL could not be parsed safely: {exc}") from exc

    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is permitted.")

    expression = statements[0]
    if not isinstance(expression, _ALLOWED_QUERY_ROOTS):
        raise SQLValidationError("Only SELECT queries and SELECT UNION queries are permitted.")

    if any(expression.find(node_type) is not None for node_type in _FORBIDDEN_SQL_NODES):
        raise SQLValidationError("The query contains a non-read-only SQL operation.")

    cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
    referenced_tables = {
        table.name.lower()
        for table in expression.find_all(exp.Table)
        if table.name.lower() not in cte_names
    }
    unknown_tables = referenced_tables - ALLOWED_SQL_TABLES
    if unknown_tables:
        table_list = ", ".join(sorted(unknown_tables))
        raise SQLValidationError(f"Query references table(s) outside the allow-list: {table_list}.")

    return True
