from __future__ import annotations

import os
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from .json_store import JsonConversationStore
from .postgres_store import resolve_postgres_dsn

_LAST_CONV_STORE_ERROR: str | None = None

# Process-level cache so we only pay the Postgres init cost once per Streamlit
# process. Each fresh browser tab creates a new st.session_state, so without
# this cache every new tab would re-issue the slow Postgres connect attempt.
_CACHE_LOCK = threading.Lock()
_CACHED_DSN: str | None = None
_CACHED_STORE = None
_CACHED_FALLBACK_REASON: str | None = None


def peek_conversation_store_error() -> str | None:
    """Last PostgreSQL store initialization error, if any (for UI diagnostics)."""
    return _LAST_CONV_STORE_ERROR


def reset_conversation_store_cache() -> None:
    """Clear the process-level store cache (used after env changes / for tests)."""
    global _CACHED_DSN, _CACHED_STORE, _CACHED_FALLBACK_REASON
    with _CACHE_LOCK:
        _CACHED_DSN = None
        _CACHED_STORE = None
        _CACHED_FALLBACK_REASON = None


def get_conversation_store():
    """Return a conversation store, preferring Postgres but falling back to
    a JSON file store if the Postgres init exceeds ``MERIDIAN_PG_INIT_TIMEOUT_SECONDS``.

    The decision is cached at module level keyed by DSN so subsequent calls
    (including new Streamlit sessions in the same process) are instantaneous.
    """
    global _LAST_CONV_STORE_ERROR, _CACHED_DSN, _CACHED_STORE, _CACHED_FALLBACK_REASON

    dsn = resolve_postgres_dsn(os.getenv("DATABASE_URL")) or ""

    with _CACHE_LOCK:
        if _CACHED_STORE is not None and _CACHED_DSN == dsn:
            _LAST_CONV_STORE_ERROR = _CACHED_FALLBACK_REASON
            return _CACHED_STORE

    _LAST_CONV_STORE_ERROR = None
    fallback_reason: str | None = None
    store = None

    if dsn:
        timeout_s = float(os.getenv("MERIDIAN_PG_INIT_TIMEOUT_SECONDS", "3"))
        try:
            from .postgres_store import PostgresConversationStore

            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(PostgresConversationStore, dsn)
            try:
                store = future.result(timeout=timeout_s)
            except TimeoutError:
                # The underlying thread cannot be force-killed in Python, but
                # we stop waiting and let it die in the background.
                executor.shutdown(wait=False, cancel_futures=True)
                raise TimeoutError(
                    f"PostgreSQL conversation store init exceeded {timeout_s:g}s"
                )
            executor.shutdown(wait=False)
        except Exception as e:
            fallback_reason = str(e)
            warnings.warn(
                f"PostgreSQL conversation store failed ({e!s}); "
                "falling back to JSON file store.",
                stacklevel=2,
            )
            store = None

    if store is None:
        store = JsonConversationStore()

    with _CACHE_LOCK:
        _CACHED_DSN = dsn
        _CACHED_STORE = store
        _CACHED_FALLBACK_REASON = fallback_reason

    _LAST_CONV_STORE_ERROR = fallback_reason
    return store
