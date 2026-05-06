from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.ontology import ensure_ontology  # noqa: E402
from src.store.postgres_store import resolve_postgres_dsn  # noqa: E402


def main() -> None:
    load_dotenv(REPO_ROOT / ".env", override=True)
    dsn = resolve_postgres_dsn(None)
    if not dsn:
        raise SystemExit("No Postgres DSN found. Set MERIDIAN_PG_* or DATABASE_URL in .env.")

    with psycopg.connect(dsn) as conn:
        ensure_ontology(conn, raw_deals_table="deals", raw_reps_table="reps")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name "
                "FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name LIKE 'onto\\_%' "
                "ORDER BY table_name"
            )
            print("Ontology tables:", [r[0] for r in cur.fetchall()])


if __name__ == "__main__":
    main()

