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


def _ts_iso(v: Any) -> str:
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _with_default_sslmode(url: str, *, default: str = "require") -> str:
    """Ensure sslmode is set (Supabase expects SSL for remote connections)."""
    parsed = urlparse(url)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    q.setdefault("sslmode", default)
    # Avoid hanging indefinitely on bad networks/DNS/firewalls.
    q.setdefault("connect_timeout", os.getenv("MERIDIAN_PG_CONNECT_TIMEOUT", "8"))
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
        )

    url = (url_or_none or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return None
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return _with_default_sslmode(url)
    return url


class PostgresConversationStore:
    def __init__(self, dsn: str) -> None:
        import psycopg  # type: ignore[reportMissingImports]

        self._psycopg = psycopg
        self._dsn = dsn
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()

    def list_threads(self) -> list[ChatThread]:
        result: list[ChatThread] = []
        with self._psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, created_at, updated_at, summary_checkpoint, structured_checkpoint "
                    "FROM chat_threads ORDER BY updated_at DESC"
                )
                trows = cur.fetchall()
                for row in trows:
                    tid = row[0]
                    cur.execute(
                        "SELECT role, content, created_at, result_snapshot FROM chat_messages "
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
        with self._psycopg.connect(self._dsn) as conn:
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
                    "SELECT role, content, created_at, result_snapshot FROM chat_messages "
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
        with self._psycopg.connect(self._dsn) as conn:
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
                        INSERT INTO chat_messages (thread_id, role, content, created_at, result_snapshot)
                        VALUES (%s, %s, %s, %s::timestamptz, %s::jsonb)
                        """,
                        (
                            thread.id,
                            m.role,
                            m.content,
                            m.created_at,
                            json.dumps(rs) if rs is not None else None,
                        ),
                    )
            conn.commit()

    def delete_thread(self, thread_id: str) -> None:
        with self._psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_threads WHERE id = %s", (thread_id,))
            conn.commit()
