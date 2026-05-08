r"""Smoke test for ontology + skill agent tools.

Runs the new tool handlers directly (no LLM round-trip) and prints concise
PASS/FAIL lines so we can verify catalog allowlisting, pagination clamping,
and `delete_skill` against the live Postgres ontology.

Usage (from repo root):

    .\.venv\Scripts\python.exe scripts\smoke_ontology_tools.py
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


from src.agent.tools import execute_tool  # noqa: E402


_PASSED = 0
_FAILED = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        _FAILED += 1
        print(f"FAIL  {label}  {detail}")


def main() -> int:
    print("=== list_ontology_objects ===")
    try:
        result = execute_tool("list_ontology_objects", {})
        _check("ok flag", bool(result.get("ok")), str(result.get("error", ""))[:120])
        objects = result.get("objects") or []
        names = {o.get("name") for o in objects if isinstance(o, dict)}
        _check("includes 'deal'", "deal" in names, f"got {sorted(names)}")
        _check("includes 'rep'", "rep" in names)
        _check("includes 'rep_quota'", "rep_quota" in names)
    except Exception as exc:
        _check("list_ontology_objects raised", False, repr(exc))

    print("\n=== read_ontology_schema (valid) ===")
    schema_result = execute_tool("read_ontology_schema", {"object_name": "deal"})
    _check("ok flag", bool(schema_result.get("ok")), str(schema_result.get("error", ""))[:120])
    schema = schema_result.get("schema") or {}
    attr_names = {a.get("name") for a in schema.get("attributes", [])}
    _check("deal_id present", "deal_id" in attr_names)
    _check("stage present", "stage" in attr_names)
    _check("table_name onto_deal", schema.get("table_name") == "onto_deal", str(schema.get("table_name")))

    print("\n=== read_ontology_schema (invalid) ===")
    bad_schema = execute_tool("read_ontology_schema", {"object_name": "not_a_thing"})
    _check("rejects unknown object", bad_schema.get("ok") is False, str(bad_schema)[:160])

    print("\n=== read_ontology_data (valid columns + small page) ===")
    data_ok = execute_tool(
        "read_ontology_data",
        {"object_name": "deal", "columns": ["deal_id", "stage", "deal_value"], "offset": 0, "limit": 5},
    )
    _check("ok flag", bool(data_ok.get("ok")), str(data_ok.get("error", ""))[:200])
    rows = data_ok.get("rows") or []
    _check("returns at most 5 rows", len(rows) <= 5, f"got {len(rows)}")
    if rows:
        first = rows[0]
        _check("only requested columns returned", set(first.keys()) <= {"deal_id", "stage", "deal_value"}, str(set(first.keys())))

    print("\n=== read_ontology_data (no columns -> all attributes) ===")
    data_default = execute_tool(
        "read_ontology_data",
        {"object_name": "rep", "limit": 3},
    )
    _check("ok flag", bool(data_default.get("ok")), str(data_default.get("error", ""))[:200])
    cols = data_default.get("columns") or []
    _check(
        "default columns include rep_id",
        "rep_id" in cols,
        f"cols={cols}",
    )

    print("\n=== read_ontology_data (invalid column) ===")
    bad_col = execute_tool(
        "read_ontology_data",
        {"object_name": "deal", "columns": ["deal_id", "totally_made_up_col"], "limit": 1},
    )
    _check("rejects unknown column", bad_col.get("ok") is False, str(bad_col)[:200])

    print("\n=== read_ontology_data (limit clamped) ===")
    big_limit = execute_tool(
        "read_ontology_data",
        {"object_name": "deal", "limit": 9999},
    )
    _check("ok flag", bool(big_limit.get("ok")), str(big_limit.get("error", ""))[:200])
    _check("limit clamped to <=50", int(big_limit.get("limit") or 0) <= 50, f"limit={big_limit.get('limit')}")

    print("\n=== read_ontology_data (invalid object) ===")
    bad_obj = execute_tool("read_ontology_data", {"object_name": "definitely_not_real"})
    _check("rejects unknown object", bad_obj.get("ok") is False, str(bad_obj)[:200])

    print("\n=== read_ontology_data (filtered + ordered) ===")
    filtered = execute_tool(
        "read_ontology_data",
        {
            "object_name": "deal",
            "columns": ["deal_id", "stage", "close_date"],
            "filters": [
                {"column": "stage", "operator": "=", "value": "Closed Won"},
            ],
            "order_by": [{"column": "close_date", "direction": "desc"}],
            "limit": 5,
        },
    )
    _check("ok flag", bool(filtered.get("ok")), str(filtered.get("error", ""))[:200])
    f_rows = filtered.get("rows") or []
    _check("at most 5 rows", len(f_rows) <= 5, f"got {len(f_rows)}")
    _check(
        "every row has stage=Closed Won",
        all(r.get("stage") == "Closed Won" for r in f_rows) if f_rows else True,
        f"sample={f_rows[:2]}",
    )

    print("\n=== aggregate_ontology_data (sum + count, filtered) ===")
    sum_q = execute_tool(
        "aggregate_ontology_data",
        {
            "object_name": "deal",
            "aggregations": [
                {"function": "sum", "column": "deal_value", "alias": "pipeline_total"},
                {"function": "count", "alias": "deal_count"},
            ],
            "filters": [
                {"column": "close_date", "operator": "<", "value": "2026-04-01"},
                {
                    "column": "stage",
                    "operator": "in",
                    "values": ["Prospecting", "Discovery", "Proposal", "Negotiation"],
                },
            ],
        },
    )
    _check("ok flag", bool(sum_q.get("ok")), str(sum_q.get("error", ""))[:200])
    sum_rows = sum_q.get("rows") or []
    _check("returns exactly one summary row", len(sum_rows) == 1, f"rows={sum_rows}")
    if sum_rows:
        row = sum_rows[0]
        pipeline_total = row.get("pipeline_total")
        deal_count = row.get("deal_count")
        _check(
            "pipeline_total is a finite number",
            isinstance(pipeline_total, (int, float)) and pipeline_total >= 0,
            f"pipeline_total={pipeline_total!r}",
        )
        _check(
            "deal_count is a non-negative int",
            isinstance(deal_count, int) and deal_count >= 0,
            f"deal_count={deal_count!r}",
        )

    print("\n=== aggregate_ontology_data (group_by region, count, ordered desc) ===")
    grouped = execute_tool(
        "aggregate_ontology_data",
        {
            "object_name": "deal",
            "aggregations": [{"function": "count", "alias": "deal_count"}],
            "group_by": ["region_id"],
            "order_by": [{"column": "deal_count", "direction": "desc"}],
            "limit": 10,
        },
    )
    _check("ok flag", bool(grouped.get("ok")), str(grouped.get("error", ""))[:200])
    g_rows = grouped.get("rows") or []
    _check(
        "every grouped row has region_id and deal_count",
        all("region_id" in r and "deal_count" in r for r in g_rows) if g_rows else False,
        f"rows={g_rows[:2]}",
    )
    _check(
        "grouped rows are sorted desc by deal_count",
        all(g_rows[i]["deal_count"] >= g_rows[i + 1]["deal_count"] for i in range(len(g_rows) - 1)),
        f"counts={[r.get('deal_count') for r in g_rows]}",
    )

    print("\n=== aggregate_ontology_data rejects sum on TEXT column ===")
    bad_sum = execute_tool(
        "aggregate_ontology_data",
        {
            "object_name": "deal",
            "aggregations": [{"function": "sum", "column": "stage"}],
        },
    )
    _check("rejects sum on TEXT", bad_sum.get("ok") is False, str(bad_sum)[:200])

    print("\n=== aggregate_ontology_data rejects unknown filter operator ===")
    bad_op = execute_tool(
        "aggregate_ontology_data",
        {
            "object_name": "deal",
            "aggregations": [{"function": "count"}],
            "filters": [{"column": "stage", "operator": "REGEX", "value": ".*"}],
        },
    )
    _check("rejects unknown operator", bad_op.get("ok") is False, str(bad_op)[:200])

    print("\n=== aggregate_ontology_data rejects unknown column ===")
    bad_agg_col = execute_tool(
        "aggregate_ontology_data",
        {
            "object_name": "deal",
            "aggregations": [{"function": "sum", "column": "totally_fake"}],
        },
    )
    _check("rejects unknown agg column", bad_agg_col.get("ok") is False, str(bad_agg_col)[:200])

    print("\n=== aggregate_ontology_data clamps limit ===")
    big = execute_tool(
        "aggregate_ontology_data",
        {
            "object_name": "deal",
            "aggregations": [{"function": "count", "alias": "deal_count"}],
            "group_by": ["stage"],
            "limit": 9999,
        },
    )
    _check("ok flag", bool(big.get("ok")), str(big.get("error", ""))[:200])
    _check(
        "row_count under MAX_READ_LIMIT",
        int(big.get("row_count") or 0) <= 50,
        f"row_count={big.get('row_count')}",
    )

    print("\n=== create_skill + list_skills + delete_skill round trip ===")
    test_skill_id = "smoke_test_skill_tmp"
    created = execute_tool(
        "create_skill",
        {
            "skill_id": test_skill_id,
            "skill_name": "Smoke test skill",
            "skill_debrief": "Temporary skill used by scripts/smoke_ontology_tools.py.",
            "skill_content": "If you see this skill in production, something is wrong.",
        },
    )
    _check("create_skill ok", bool(created.get("ok")), str(created.get("error", ""))[:200])

    listed = execute_tool("list_skills", {})
    _check("list_skills ok", bool(listed.get("ok")), str(listed.get("error", ""))[:200])
    skills = listed.get("skills") or []
    found_ids = {s.get("skill_id") for s in skills if isinstance(s, dict)}
    _check(
        "list_skills includes new skill",
        test_skill_id in found_ids,
        f"count={listed.get('count')} ids={sorted(found_ids)}",
    )
    if skills:
        sample = skills[0]
        _check(
            "list_skills omits skill_content",
            "skill_content" not in sample,
            str(set(sample.keys())),
        )

    deleted = execute_tool("delete_skill", {"skill_id": test_skill_id})
    _check("delete_skill ok", bool(deleted.get("ok")), str(deleted.get("error", ""))[:200])
    _check("delete_skill removed row", deleted.get("deleted") is True, str(deleted))

    deleted_again = execute_tool("delete_skill", {"skill_id": test_skill_id})
    _check(
        "second delete reports not-found",
        deleted_again.get("ok") is True and deleted_again.get("deleted") is False,
        str(deleted_again),
    )

    after_delete = execute_tool("list_skills", {})
    after_ids = {s.get("skill_id") for s in (after_delete.get("skills") or []) if isinstance(s, dict)}
    _check(
        "list_skills no longer includes deleted skill",
        test_skill_id not in after_ids,
        f"ids={sorted(after_ids)}",
    )

    print(f"\n=== summary: {_PASSED} passed, {_FAILED} failed ===")
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
