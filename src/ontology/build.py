from __future__ import annotations

"""
Build/refresh ontology tables in Postgres from raw `deals` + `reps`.

The goal is to keep raw tables as the source-of-truth inputs, but compute metrics
against stable ontology entities (onto_* tables).
"""

from pathlib import Path


_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def ensure_ontology(conn, *, raw_deals_table: str = "deals", raw_reps_table: str = "reps") -> None:
    """
    Create ontology tables (if missing) and upsert contents from raw tables.

    Assumes raw tables have the same columns as the CSV version.
    """
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)

        # Regions & segments (dimensions).
        cur.execute(
            f"""
            INSERT INTO onto_region (region_id, region_name)
            SELECT DISTINCT region AS region_id, region AS region_name
            FROM {raw_reps_table}
            WHERE region IS NOT NULL AND TRIM(region) <> ''
            ON CONFLICT (region_id) DO UPDATE SET region_name = EXCLUDED.region_name
            """
        )
        cur.execute(
            f"""
            INSERT INTO onto_segment (segment_id, segment_name)
            SELECT DISTINCT segment AS segment_id, segment AS segment_name
            FROM {raw_reps_table}
            WHERE segment IS NOT NULL AND TRIM(segment) <> ''
            ON CONFLICT (segment_id) DO UPDATE SET segment_name = EXCLUDED.segment_name
            """
        )

        # Accounts from deals.account_name.
        cur.execute(
            f"""
            INSERT INTO onto_account (account_id, account_name)
            SELECT DISTINCT account_name AS account_id, account_name AS account_name
            FROM {raw_deals_table}
            WHERE account_name IS NOT NULL AND TRIM(account_name) <> ''
            ON CONFLICT (account_id) DO UPDATE SET account_name = EXCLUDED.account_name
            """
        )

        # Managers from reps.manager (name in this dataset).
        cur.execute(
            f"""
            INSERT INTO onto_manager (manager_id, manager_name)
            SELECT DISTINCT manager AS manager_id, manager AS manager_name
            FROM {raw_reps_table}
            WHERE manager IS NOT NULL AND TRIM(manager) <> ''
            ON CONFLICT (manager_id) DO UPDATE SET manager_name = EXCLUDED.manager_name
            """
        )

        # Reps.
        cur.execute(
            f"""
            INSERT INTO onto_rep (rep_id, rep_name, hire_date, manager_id, region_id, segment_id)
            SELECT
              rep_id,
              rep_name,
              hire_date::date,
              manager AS manager_id,
              region AS region_id,
              segment AS segment_id
            FROM {raw_reps_table}
            ON CONFLICT (rep_id) DO UPDATE SET
              rep_name = EXCLUDED.rep_name,
              hire_date = EXCLUDED.hire_date,
              manager_id = EXCLUDED.manager_id,
              region_id = EXCLUDED.region_id,
              segment_id = EXCLUDED.segment_id
            """
        )

        # Quotas → onto_rep_quota for 2026Q1.
        cur.execute(
            f"""
            INSERT INTO onto_rep_quota (rep_id, period, quota)
            SELECT rep_id, '2026Q1' AS period, quota_q1_2026::numeric AS quota
            FROM {raw_reps_table}
            WHERE quota_q1_2026 IS NOT NULL
            ON CONFLICT (rep_id, period) DO UPDATE SET quota = EXCLUDED.quota
            """
        )

        # Deals fact.
        cur.execute(
            f"""
            INSERT INTO onto_deal (
              deal_id,
              account_id,
              rep_id,
              region_id,
              segment_id,
              stage,
              deal_value,
              close_date,
              created_date,
              product_line,
              loss_reason
            )
            SELECT
              deal_id,
              account_name AS account_id,
              rep_id,
              region AS region_id,
              segment AS segment_id,
              stage,
              deal_value::numeric,
              close_date::date,
              created_date::date,
              product_line,
              loss_reason
            FROM {raw_deals_table}
            ON CONFLICT (deal_id) DO UPDATE SET
              account_id = EXCLUDED.account_id,
              rep_id = EXCLUDED.rep_id,
              region_id = EXCLUDED.region_id,
              segment_id = EXCLUDED.segment_id,
              stage = EXCLUDED.stage,
              deal_value = EXCLUDED.deal_value,
              close_date = EXCLUDED.close_date,
              created_date = EXCLUDED.created_date,
              product_line = EXCLUDED.product_line,
              loss_reason = EXCLUDED.loss_reason
            """
        )

    conn.commit()

