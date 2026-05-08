"""PostgreSQL store for reusable prompt skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..store.postgres_store import _apply_session_timeouts, resolve_postgres_dsn

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_SCHEMA_APPLIED: set[str] = set()


@dataclass(frozen=True)
class SkillSummary:
    skill_id: str
    skill_name: str
    skill_debrief: str


@dataclass(frozen=True)
class Skill(SkillSummary):
    skill_content: str


def _row_to_summary(row: Any) -> SkillSummary:
    return SkillSummary(
        skill_id=row[0],
        skill_name=row[1],
        skill_debrief=row[2],
    )


def _row_to_skill(row: Any) -> Skill:
    return Skill(
        skill_id=row[0],
        skill_name=row[1],
        skill_debrief=row[2],
        skill_content=row[3],
    )


class PostgresSkillStore:
    def __init__(self, dsn: str | None = None) -> None:
        import os
        import psycopg  # type: ignore[reportMissingImports]

        resolved_dsn = resolve_postgres_dsn(dsn)
        if not resolved_dsn:
            raise ValueError("PostgresSkillStore requires DATABASE_URL or MERIDIAN_PG_* env vars.")

        self._psycopg = psycopg
        self._dsn = resolved_dsn
        if os.getenv("MERIDIAN_PG_AUTOMIGRATE", "1") != "0":
            self._ensure_schema()

    def _connect(self):
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

    def list_skills(self) -> list[SkillSummary]:
        """Return the lightweight skill catalog without full prompt content."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT skill_id, skill_name, skill_debrief
                    FROM skills
                    ORDER BY skill_name ASC
                    """
                )
                return [_row_to_summary(row) for row in cur.fetchall()]

    def get_skill(self, skill_id: str) -> Skill | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT skill_id, skill_name, skill_debrief, skill_content
                    FROM skills
                    WHERE skill_id = %s
                    """,
                    (skill_id,),
                )
                row = cur.fetchone()
        return _row_to_skill(row) if row else None

    def upsert_skill(self, skill: Skill) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO skills (skill_id, skill_name, skill_debrief, skill_content)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (skill_id) DO UPDATE SET
                      skill_name = EXCLUDED.skill_name,
                      skill_debrief = EXCLUDED.skill_debrief,
                      skill_content = EXCLUDED.skill_content,
                      updated_at = now()
                    """,
                    (
                        skill.skill_id,
                        skill.skill_name,
                        skill.skill_debrief,
                        skill.skill_content,
                    ),
                )

    def delete_skill(self, skill_id: str) -> bool:
        """Delete the skill row. Returns True if a row was removed, False otherwise."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM skills WHERE skill_id = %s", (skill_id,))
                return (cur.rowcount or 0) > 0
