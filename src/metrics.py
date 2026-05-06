"""
Ground-truth metrics from deals + reps (CSV by default, or PostgreSQL).

Definitions match ANALYTICS_CONTRACT.md (Q1 2026 on close_date, stages, pipeline, quota).

Postgres: set ``MERIDIAN_DATA_SOURCE=postgres`` and ``DATABASE_URL`` (or ``MERIDIAN_DATA_DATABASE_URL``).
Tables default to ``deals`` and ``reps``; override with ``MERIDIAN_DEALS_TABLE`` / ``MERIDIAN_REPS_TABLE``.
Schema must match the CSV columns the app already expects.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import pandas as pd

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _sanitize_sql_identifier(name: str, *, fallback: str) -> str:
    """Allow only simple identifiers for table names from env (avoid SQL injection)."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip()):
        return name.strip()
    return fallback


Q1_START = pd.Timestamp("2026-01-01")
Q1_END = pd.Timestamp("2026-03-31")

CLOSED_WON = "Closed Won"
CLOSED_LOST = "Closed Lost"
OPEN_STAGES = frozenset({"Prospecting", "Discovery", "Proposal", "Negotiation"})


def _after_load_typing(deals: pd.DataFrame, reps: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Normalize dtypes after CSV or SQL load."""
    deals["close_date"] = pd.to_datetime(deals["close_date"])
    deals["created_date"] = pd.to_datetime(deals["created_date"])
    reps["hire_date"] = pd.to_datetime(reps["hire_date"])
    deals["deal_value"] = pd.to_numeric(deals["deal_value"], errors="coerce")
    reps["quota_q1_2026"] = pd.to_numeric(reps["quota_q1_2026"], errors="coerce")
    return deals, reps


def _postgres_dsn() -> str | None:
    return (os.getenv("MERIDIAN_DATA_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip() or None


def load_data_from_postgres(
    dsn: str | None = None,
    *,
    deals_table: str | None = None,
    reps_table: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load ``deals`` and ``reps`` from PostgreSQL.
    Use the same connection string as Supabase/Neon; table names must match your DB.
    """
    import psycopg  # type: ignore[reportMissingImports]

    url = dsn or _postgres_dsn()
    if not url:
        raise ValueError(
            "load_data_from_postgres requires a DSN or DATABASE_URL / MERIDIAN_DATA_DATABASE_URL."
        )
    dt = _sanitize_sql_identifier(
        deals_table or os.getenv("MERIDIAN_DEALS_TABLE") or "deals", fallback="deals"
    )
    rt = _sanitize_sql_identifier(
        reps_table or os.getenv("MERIDIAN_REPS_TABLE") or "reps", fallback="reps"
    )

    use_ontology = (os.getenv("MERIDIAN_USE_ONTOLOGY") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    with psycopg.connect(url) as conn:
        if use_ontology:
            from .ontology import ensure_ontology

            ensure_ontology(conn, raw_deals_table=dt, raw_reps_table=rt)
            deals = pd.read_sql_query(
                "SELECT "
                "deal_id, "
                "account_id AS account_name, "
                "rep_id, "
                "(SELECT region_name FROM onto_region r WHERE r.region_id = d.region_id) AS region, "
                "(SELECT segment_name FROM onto_segment s WHERE s.segment_id = d.segment_id) AS segment, "
                "stage, deal_value, close_date, created_date, product_line, loss_reason "
                "FROM onto_deal d",
                conn,
            )
            reps = pd.read_sql_query(
                "SELECT "
                "rep_id, "
                "rep_name, "
                "(SELECT segment_name FROM onto_segment s WHERE s.segment_id = r.segment_id) AS segment, "
                "(SELECT region_name FROM onto_region g WHERE g.region_id = r.region_id) AS region, "
                "hire_date, "
                "(SELECT quota FROM onto_rep_quota q WHERE q.rep_id = r.rep_id AND q.period = '2026Q1') AS quota_q1_2026 "
                "FROM onto_rep r",
                conn,
            )
        else:
            deals = pd.read_sql_query(f"SELECT * FROM {dt}", conn)
            reps = pd.read_sql_query(f"SELECT * FROM {rt}", conn)
    return _after_load_typing(deals, reps)


def load_data(data_dir: Path | str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load deal and rep tables.

    * Default: ``data/deals.csv`` and ``data/reps.csv`` (or ``data_dir`` if passed).
    * PostgreSQL: set env ``MERIDIAN_DATA_SOURCE=postgres`` and ``DATABASE_URL`` (or
      ``MERIDIAN_DATA_DATABASE_URL`` for data only, e.g. read replica).
    """
    source = (os.getenv("MERIDIAN_DATA_SOURCE") or "csv").strip().lower()
    if source in ("postgres", "postgresql", "pg", "db", "supabase"):
        dsn = _postgres_dsn()
        if not dsn:
            raise ValueError(
                "MERIDIAN_DATA_SOURCE=postgres requires DATABASE_URL or MERIDIAN_DATA_DATABASE_URL."
            )
        return load_data_from_postgres(dsn)
    root = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    deals = pd.read_csv(root / "deals.csv")
    reps = pd.read_csv(root / "reps.csv")
    return _after_load_typing(deals, reps)


def stage_kind(stage: str) -> Literal["open", "closed_won", "closed_lost", "unknown"]:
    if stage == CLOSED_WON:
        return "closed_won"
    if stage == CLOSED_LOST:
        return "closed_lost"
    if stage in OPEN_STAGES:
        return "open"
    return "unknown"


def mask_q1_2026(close_dates: pd.Series) -> pd.Series:
    """Inclusive close_date window per contract §1."""
    return (close_dates >= Q1_START) & (close_dates <= Q1_END)


def deals_closed_won_q1(deals: pd.DataFrame) -> pd.DataFrame:
    """Closed Won with close_date in Q1 2026."""
    m = (deals["stage"] == CLOSED_WON) & mask_q1_2026(deals["close_date"])
    return deals.loc[m].copy()


def pipeline_open_deals(
    deals: pd.DataFrame,
    *,
    close_date_on_or_before: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    Open pipeline: stages in OPEN_STAGES only (§3).
    Optional: restrict to expected close on/before a date (e.g. end of March).
    """
    m = deals["stage"].isin(OPEN_STAGES)
    out = deals.loc[m].copy()
    if close_date_on_or_before is not None:
        out = out.loc[out["close_date"] <= close_date_on_or_before]
    return out


def quota_attainment_q1_by_rep(deals: pd.DataFrame, reps: pd.DataFrame) -> pd.DataFrame:
    """
    Per rep: sum Closed Won deal_value in Q1 / quota_q1_2026 (§4).
    Returns one row per rep with booked_q1, attainment ratio (0–1+).
    """
    booked = (
        deals_closed_won_q1(deals)
        .groupby("rep_id", as_index=False)["deal_value"]
        .sum()
        .rename(columns={"deal_value": "booked_q1"})
    )
    out = reps.merge(booked, on="rep_id", how="left")
    out["booked_q1"] = out["booked_q1"].fillna(0)
    out["attainment"] = out["booked_q1"] / out["quota_q1_2026"]
    return out[
        [
            "rep_id",
            "rep_name",
            "segment",
            "region",
            "quota_q1_2026",
            "booked_q1",
            "attainment",
        ]
    ]


def enterprise_segment_q1_attainment(deals: pd.DataFrame, reps: pd.DataFrame) -> dict:
    """
    Enterprise: Closed Won Q1 sum where deals.segment == Enterprise (§5),
    vs sum of quota_q1_2026 for reps whose book is Enterprise (reps.segment).
    """
    ent_deals = deals_closed_won_q1(deals).loc[lambda d: d["segment"] == "Enterprise"]
    booked = float(ent_deals["deal_value"].sum())
    ent_reps = reps.loc[reps["segment"] == "Enterprise"]
    quota_sum = float(ent_reps["quota_q1_2026"].sum())
    ratio = booked / quota_sum if quota_sum else float("nan")
    return {
        "segment": "Enterprise",
        "booked_q1_closed_won": booked,
        "quota_sum_q1_reps": quota_sum,
        "attainment": ratio,
        "deal_rows": ent_deals,
        "rep_rows": ent_reps,
    }


def reps_at_risk_missing_q1_quota(
    deals: pd.DataFrame,
    reps: pd.DataFrame,
    *,
    as_of_attainment_less_than: float = 1.0,
) -> pd.DataFrame:
    """
    Reps whose Q1 attainment is strictly below the threshold (default: full quota = 1.0).
    Interpret as 'at risk of missing Q1' only if you state this rule to users.
    """
    q = quota_attainment_q1_by_rep(deals, reps)
    return q.loc[q["attainment"] < as_of_attainment_less_than].sort_values(
        "attainment", ascending=True
    )


def ironbridge_deal(deals: pd.DataFrame) -> pd.DataFrame:
    """Rows for account Ironbridge (loss_reason may be blank — §7)."""
    return deals.loc[deals["account_name"].str.strip().eq("Ironbridge")].copy()


def pipeline_value_before_end_of_march(deals: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    """Sample question: open pipeline with expected close on/before 2026-03-31 (§3 example)."""
    sub = pipeline_open_deals(deals, close_date_on_or_before=pd.Timestamp("2026-03-31"))
    return float(sub["deal_value"].sum()), sub
