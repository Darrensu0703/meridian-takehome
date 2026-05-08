"""PostgreSQL conversation store — set DATABASE_URL (e.g. Neon free tier)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..conversation_models import ChatMessage, ChatThread

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _structured_from_row(val: Any) -> dict[str, Any]:
    if val is None:
        return {}
    if hasattr(val, "keys"):
        return dict(val)
    if isinstance(val, str):
        return json.loads(val)
    return {}


def _list_from_row(val: Any) -> list[Any]:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        parsed = json.loads(val)
        return parsed if isinstance(parsed, list) else []
    return []


def _ts_iso(v: Any) -> str:
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _server_options() -> str:
    """Build a Postgres ``options`` string with statement and lock timeouts.

    ``lock_timeout`` is critical: without it, DDL like ``ALTER TABLE`` will
    wait indefinitely behind any other reader (e.g., a previous Streamlit
    process that died with an open transaction), which manifested as the
    schema migration hanging for minutes on Supabase.
    """
    statement_ms = os.getenv("MERIDIAN_PG_STATEMENT_TIMEOUT_MS", "5000")
    lock_ms = os.getenv("MERIDIAN_PG_LOCK_TIMEOUT_MS", "2000")
    idle_tx_ms = os.getenv("MERIDIAN_PG_IDLE_TX_TIMEOUT_MS", "10000")
    return (
        f"-c statement_timeout={statement_ms} "
        f"-c lock_timeout={lock_ms} "
        f"-c idle_in_transaction_session_timeout={idle_tx_ms}"
    )


def _with_default_sslmode(url: str, *, default: str = "require") -> str:
    """Ensure sslmode is set (Supabase expects SSL for remote connections)."""
    parsed = urlparse(url)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    q.setdefault("sslmode", default)
    # Avoid hanging indefinitely on bad networks/DNS/firewalls.
    q.setdefault("connect_timeout", os.getenv("MERIDIAN_PG_CONNECT_TIMEOUT", "8"))
    q.setdefault("options", _server_options())
    new_query = urlencode(q)
    return urlunparse(parsed._replace(query=new_query))


def resolve_postgres_dsn(url_or_none: str | None) -> str | None:
    """
    Build a psycopg connection string.

    Priority:
      1) ``MERIDIAN_PG_HOST`` + ``MERIDIAN_PG_PASSWORD`` (plain password; avoids URI encoding mistakes)
      2) ``DATABASE_URL``-style URI passed in ``url_or_none``
    """
    import psycopg  # type: ignore[reportMissingImports]

    host = (os.getenv("MERIDIAN_PG_HOST") or "").strip()
    password = (os.getenv("MERIDIAN_PG_PASSWORD") or os.getenv("PGPASSWORD") or "").strip()
    if host and password:
        user = (os.getenv("MERIDIAN_PG_USER") or "postgres").strip()
        dbname = (os.getenv("MERIDIAN_PG_DB") or "postgres").strip()
        port = (os.getenv("MERIDIAN_PG_PORT") or "5432").strip()
        sslmode = (os.getenv("MERIDIAN_PG_SSLMODE") or "require").strip()
        connect_timeout = (os.getenv("MERIDIAN_PG_CONNECT_TIMEOUT") or "8").strip()
        return psycopg.conninfo.make_conninfo(
            host=host,
            user=user,
            dbname=dbname,
            port=port,
            password=password,
            sslmode=sslmode,
            connect_timeout=connect_timeout,
            options=_server_options(),
        )

    url = (url_or_none or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return None
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return _with_default_sslmode(url)
    return url


_SCHEMA_APPLIED: set[str] = set()


def _apply_session_timeouts(conn: Any) -> None:
    """Apply timeouts via SET at session start (single round-trip).

    Supabase's supavisor pooler silently drops the libpq ``options`` startup
    parameter, so we issue ``SET`` after connecting. Combining the three SETs
    into one ``cur.execute`` keeps it to one server round-trip (matters a lot
    over the WAN to ``aws-1-us-east-2``).
    """
    statement_ms = int(os.getenv("MERIDIAN_PG_STATEMENT_TIMEOUT_MS", "5000"))
    lock_ms = int(os.getenv("MERIDIAN_PG_LOCK_TIMEOUT_MS", "2000"))
    idle_tx_ms = int(os.getenv("MERIDIAN_PG_IDLE_TX_TIMEOUT_MS", "10000"))
    with conn.cursor() as cur:
        cur.execute(
            f"SET statement_timeout = {statement_ms}; "
            f"SET lock_timeout = {lock_ms}; "
            f"SET idle_in_transaction_session_timeout = {idle_tx_ms};"
        )


class PostgresConversationStore:
    def __init__(self, dsn: str) -> None:
        import psycopg  # type: ignore[reportMissingImports]

        self._psycopg = psycopg
        self._dsn = dsn
        # Schema migration runs once per process per DSN, and only if
        # MERIDIAN_PG_AUTOMIGRATE != "0". Re-running ALTER TABLE on every
        # init can hang behind unrelated readers on Supabase.
        if os.getenv("MERIDIAN_PG_AUTOMIGRATE", "1") != "0":
            self._ensure_schema()

    def _connect(self):
        # autocommit=True so individual queries don't leave the session in
        # ``idle in transaction`` between Python iterations. Multi-statement
        # writes wrap their own explicit ``with conn.transaction():`` block.
        conn = self._psycopg.connect(self._dsn, autocommit=True)
        try:
            _apply_session_timeouts(conn)
        except Exception:
            conn.close()
            raise
        return conn

    def _ensure_schema(self) -> None:
        if self._dsn in _SCHEMA_APPLIED:
            return
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        _SCHEMA_APPLIED.add(self._dsn)

    def list_thread_summaries(self) -> list["ThreadSummary"]:
        """One-query catalog of threads (no messages). Used by sidebar list."""
        from . import ThreadSummary

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, created_at, updated_at "
                    "FROM chat_threads ORDER BY updated_at DESC"
                )
                rows = cur.fetchall()
        return [
            ThreadSummary(
                id=row[0],
                title=row[1],
                created_at=_ts_iso(row[2]),
                updated_at=_ts_iso(row[3]),
            )
            for row in rows
        ]

    def list_threads(self) -> list[ChatThread]:
        result: list[ChatThread] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, created_at, updated_at, summary_checkpoint, structured_checkpoint "
                    "FROM chat_threads ORDER BY updated_at DESC"
                )
                trows = cur.fetchall()
                for row in trows:
                    tid = row[0]
                    cur.execute(
                        "SELECT role, content, created_at, result_snapshot, agent_steps FROM chat_messages "
                        "WHERE thread_id = %s ORDER BY id ASC",
                        (tid,),
                    )
                    mrows = cur.fetchall()
                    msgs = [
                        ChatMessage(
                            role=m[0],
                            content=m[1],
                            created_at=_ts_iso(m[2]),
                            result_snapshot=dict(m[3]) if m[3] is not None else None,
                            agent_steps=_list_from_row(m[4]),
                        )
                        for m in mrows
                    ]
                    result.append(
                        ChatThread(
                            id=tid,
                            title=row[1],
                            created_at=_ts_iso(row[2]),
                            updated_at=_ts_iso(row[3]),
                            messages=msgs,
                            summary_checkpoint=row[4] or "",
                            structured_checkpoint=_structured_from_row(row[5]),
                        )
                    )
        return result

    def get_thread(self, thread_id: str) -> ChatThread | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, created_at, updated_at, summary_checkpoint, structured_checkpoint "
                    "FROM chat_threads WHERE id = %s",
                    (thread_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute(
                    "SELECT role, content, created_at, result_snapshot, agent_steps FROM chat_messages "
                    "WHERE thread_id = %s ORDER BY id ASC",
                    (thread_id,),
                )
                mrows = cur.fetchall()
        msgs = [
            ChatMessage(
                role=m[0],
                content=m[1],
                created_at=_ts_iso(m[2]),
                result_snapshot=dict(m[3]) if m[3] is not None else None,
                agent_steps=_list_from_row(m[4]),
            )
            for m in mrows
        ]
        return ChatThread(
            id=row[0],
            title=row[1],
            created_at=_ts_iso(row[2]),
            updated_at=_ts_iso(row[3]),
            messages=msgs,
            summary_checkpoint=row[4] or "",
            structured_checkpoint=_structured_from_row(row[5]),
        )

    def upsert_thread(self, thread: ChatThread) -> None:
        sc_json = json.dumps(thread.structured_checkpoint)
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO chat_threads (id, title, created_at, updated_at, summary_checkpoint, structured_checkpoint)
                        VALUES (%s, %s, %s::timestamptz, %s::timestamptz, %s, %s::jsonb)
                        ON CONFLICT (id) DO UPDATE SET
                          title = EXCLUDED.title,
                          updated_at = EXCLUDED.updated_at,
                          summary_checkpoint = EXCLUDED.summary_checkpoint,
                          structured_checkpoint = EXCLUDED.structured_checkpoint
                        """,
                        (
                            thread.id,
                            thread.title,
                            thread.created_at,
                            thread.updated_at,
                            thread.summary_checkpoint,
                            sc_json,
                        ),
                    )
                    cur.execute("DELETE FROM chat_messages WHERE thread_id = %s", (thread.id,))
                    for m in thread.messages:
                        rs = m.result_snapshot
                        cur.execute(
                            """
                            INSERT INTO chat_messages (thread_id, role, content, created_at, result_snapshot, agent_steps)
                            VALUES (%s, %s, %s, %s::timestamptz, %s::jsonb, %s::jsonb)
                            """,
                            (
                                thread.id,
                                m.role,
                                m.content,
                                m.created_at,
                                json.dumps(rs) if rs is not None else None,
                                json.dumps(m.agent_steps),
                            ),
                        )

    def delete_thread(self, thread_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_threads WHERE id = %s", (thread_id,))
