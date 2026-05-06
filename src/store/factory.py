from __future__ import annotations

import os
import warnings

from .json_store import JsonConversationStore
from .postgres_store import resolve_postgres_dsn

_LAST_CONV_STORE_ERROR: str | None = None


def peek_conversation_store_error() -> str | None:
    """Last PostgreSQL store initialization error, if any (for UI diagnostics)."""
    return _LAST_CONV_STORE_ERROR


def get_conversation_store():
    global _LAST_CONV_STORE_ERROR
    _LAST_CONV_STORE_ERROR = None

    dsn = resolve_postgres_dsn(os.getenv("DATABASE_URL"))
    if dsn:
        try:
            from .postgres_store import PostgresConversationStore

            return PostgresConversationStore(dsn)
        except Exception as e:
            _LAST_CONV_STORE_ERROR = str(e)
            warnings.warn(
                f"PostgreSQL conversation store failed ({e!s}); "
                "falling back to JSON file store.",
                stacklevel=2,
            )
    return JsonConversationStore()
