from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from ..ontology import (
    DEFAULT_READ_LIMIT,
    MAX_READ_LIMIT,
    ONTOLOGY_CATALOG,
    OntologyError,
    PostgresOntologyStore,
)
from ..skills import PostgresSkillStore, Skill


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def as_openai_tool(self) -> dict[str, Any]:
        """Tool spec in OpenAI Chat Completions function-calling shape."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


_SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_DEBRIEF_MAX_CHARS = 500


def _require_string(payload: dict[str, Any], key: str, *, max_chars: int | None = None) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"`{key}` is required and must be a non-empty string.")
    out = value.strip()
    if max_chars is not None and len(out) > max_chars:
        raise ValueError(f"`{key}` must be <= {max_chars} characters.")
    return out


def _require_skill_id(payload: dict[str, Any]) -> str:
    skill_id = _require_string(payload, "skill_id", max_chars=64).lower()
    if not _SKILL_ID_RE.fullmatch(skill_id):
        raise ValueError(
            "`skill_id` must start with a letter and contain only lowercase letters, numbers, and underscores."
        )
    return skill_id


def _create_skill(payload: dict[str, Any]) -> dict[str, Any]:
    skill_id = _require_skill_id(payload)
    skill_name = _require_string(payload, "skill_name", max_chars=120)
    skill_debrief = _require_string(payload, "skill_debrief", max_chars=_DEBRIEF_MAX_CHARS)
    skill_content = _require_string(payload, "skill_content")

    store = PostgresSkillStore()
    store.upsert_skill(
        Skill(
            skill_id=skill_id,
            skill_name=skill_name,
            skill_debrief=skill_debrief,
            skill_content=skill_content,
        )
    )
    return {
        "ok": True,
        "skill_id": skill_id,
        "skill_name": skill_name,
        "skill_debrief": skill_debrief,
    }


def _delete_skill(payload: dict[str, Any]) -> dict[str, Any]:
    skill_id = _require_skill_id(payload)
    store = PostgresSkillStore()
    deleted = store.delete_skill(skill_id)
    return {
        "ok": True,
        "skill_id": skill_id,
        "deleted": deleted,
        "message": (
            f"Skill `{skill_id}` deleted." if deleted else f"No skill with id `{skill_id}` was found."
        ),
    }


def _list_skills(_payload: dict[str, Any]) -> dict[str, Any]:
    store = PostgresSkillStore()
    summaries = store.list_skills()
    return {
        "ok": True,
        "count": len(summaries),
        "skills": [
            {
                "skill_id": s.skill_id,
                "skill_name": s.skill_name,
                "skill_debrief": s.skill_debrief,
            }
            for s in summaries
        ],
    }


def _list_ontology_objects(_payload: dict[str, Any]) -> dict[str, Any]:
    store = PostgresOntologyStore()
    return {"ok": True, "objects": store.list_objects()}


def _read_ontology_schema(payload: dict[str, Any]) -> dict[str, Any]:
    object_name = _require_string(payload, "object_name", max_chars=64)
    store = PostgresOntologyStore()
    try:
        schema = store.get_schema(object_name)
    except OntologyError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "schema": schema}


def _coerce_columns(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("`columns` must be a list of strings.")
    return [str(v) for v in value]


def _read_ontology_data(payload: dict[str, Any]) -> dict[str, Any]:
    object_name = _require_string(payload, "object_name", max_chars=64)
    columns = _coerce_columns(payload.get("columns"))
    offset = payload.get("offset", 0)
    limit = payload.get("limit", DEFAULT_READ_LIMIT)
    filters = payload.get("filters")
    order_by = payload.get("order_by")

    store = PostgresOntologyStore()
    try:
        page = store.read_data(
            object_name,
            columns=columns,
            offset=offset,
            limit=limit,
            filters=filters,
            order_by=order_by,
        )
    except OntologyError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "object_name": page.object_name,
        "table_name": page.table_name,
        "columns": list(page.columns),
        "offset": page.offset,
        "limit": page.limit,
        "total_rows": page.total_rows,
        "rows": list(page.rows),
    }


def _aggregate_ontology_data(payload: dict[str, Any]) -> dict[str, Any]:
    object_name = _require_string(payload, "object_name", max_chars=64)
    aggregations = payload.get("aggregations")
    if not isinstance(aggregations, list):
        return {"ok": False, "error": "`aggregations` is required and must be a list."}
    filters = payload.get("filters")
    group_by = payload.get("group_by")
    order_by = payload.get("order_by")
    limit = payload.get("limit", MAX_READ_LIMIT)

    store = PostgresOntologyStore()
    try:
        page = store.aggregate_data(
            object_name,
            aggregations=aggregations,
            filters=filters,
            group_by=group_by,
            order_by=order_by,
            limit=limit,
        )
    except OntologyError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "object_name": page.object_name,
        "table_name": page.table_name,
        "columns": list(page.columns),
        "row_count": page.row_count,
        "rows": list(page.rows),
    }


CREATE_SKILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "skill_id": {
            "type": "string",
            "description": "Stable lowercase identifier, e.g. pipeline_risk_review.",
        },
        "skill_name": {
            "type": "string",
            "description": "Human-readable skill name.",
        },
        "skill_debrief": {
            "type": "string",
            "maxLength": _DEBRIEF_MAX_CHARS,
            "description": "Short description of what the skill does and when to use it.",
        },
        "skill_content": {
            "type": "string",
            "description": "Full prompt/instruction content for the skill.",
        },
    },
    "required": ["skill_id", "skill_name", "skill_debrief", "skill_content"],
}


DELETE_SKILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "skill_id": {
            "type": "string",
            "description": "Stable lowercase skill identifier to remove.",
        },
    },
    "required": ["skill_id"],
}


LIST_SKILLS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {},
}


LIST_ONTOLOGY_OBJECTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {},
}


READ_ONTOLOGY_SCHEMA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "object_name": {
            "type": "string",
            "enum": sorted(ONTOLOGY_CATALOG.keys()),
            "description": "Ontology object name (e.g. `deal`, `rep`).",
        },
    },
    "required": ["object_name"],
}


_FILTER_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "column": {
            "type": "string",
            "description": "Catalog attribute name to filter on.",
        },
        "operator": {
            "type": "string",
            "enum": [
                "=", "!=", "<", "<=", ">", ">=",
                "in", "not_in", "is_null", "is_not_null",
            ],
            "description": (
                "Comparison operator. Use `value` for scalar operators, "
                "`values` for `in`/`not_in`, neither for null checks."
            ),
        },
        "value": {
            "description": "Scalar value (string for TEXT/DATE, number for NUMERIC).",
        },
        "values": {
            "type": "array",
            "items": {},
            "description": "List of values for `in` / `not_in` operators.",
        },
    },
    "required": ["column", "operator"],
}


_ORDER_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "column": {
            "type": "string",
            "description": (
                "For read_ontology_data: a selected column. For "
                "aggregate_ontology_data: a group_by column or an aggregation alias."
            ),
        },
        "direction": {
            "type": "string",
            "enum": ["asc", "desc"],
            "description": "Sort direction (default asc).",
        },
    },
    "required": ["column"],
}


READ_ONTOLOGY_DATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "object_name": {
            "type": "string",
            "enum": sorted(ONTOLOGY_CATALOG.keys()),
            "description": "Ontology object name to read from.",
        },
        "columns": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional subset of attribute names. Omit to return every "
                "catalog-defined attribute."
            ),
        },
        "filters": {
            "type": "array",
            "maxItems": 8,
            "items": _FILTER_ITEM_SCHEMA,
            "description": "Optional filter clauses (AND-combined).",
        },
        "order_by": {
            "type": "array",
            "maxItems": 3,
            "items": _ORDER_ITEM_SCHEMA,
            "description": "Optional ordering over the selected columns.",
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "description": "Row offset for pagination (default 0).",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_READ_LIMIT,
            "description": (
                f"Max rows to return (default {DEFAULT_READ_LIMIT}, hard cap {MAX_READ_LIMIT})."
            ),
        },
    },
    "required": ["object_name"],
}


_AGGREGATION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "function": {
            "type": "string",
            "enum": ["count", "count_distinct", "sum", "avg", "min", "max"],
            "description": (
                "Aggregation function. `sum` and `avg` require a NUMERIC column; "
                "`count` may omit `column` to count rows."
            ),
        },
        "column": {
            "type": "string",
            "description": "Catalog attribute to aggregate over (omit only for `count`).",
        },
        "alias": {
            "type": "string",
            "pattern": r"^[a-z][a-z0-9_]{0,40}$",
            "description": (
                "Optional output column name (lowercase letters/digits/underscore). "
                "Defaults to `<function>_<column>` or `count`."
            ),
        },
    },
    "required": ["function"],
}


AGGREGATE_ONTOLOGY_DATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "object_name": {
            "type": "string",
            "enum": sorted(ONTOLOGY_CATALOG.keys()),
            "description": "Ontology object name to aggregate over.",
        },
        "aggregations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": _AGGREGATION_ITEM_SCHEMA,
            "description": "One or more aggregation expressions to compute.",
        },
        "filters": {
            "type": "array",
            "maxItems": 8,
            "items": _FILTER_ITEM_SCHEMA,
            "description": "Optional filter clauses (AND-combined).",
        },
        "group_by": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string"},
            "description": (
                "Optional catalog columns to group by. Omit for one summary row."
            ),
        },
        "order_by": {
            "type": "array",
            "maxItems": 3,
            "items": _ORDER_ITEM_SCHEMA,
            "description": (
                "Optional ordering. `column` must be a group_by column or an "
                "aggregation alias."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_READ_LIMIT,
            "description": (
                f"Max grouped rows to return (default {MAX_READ_LIMIT}, hard cap {MAX_READ_LIMIT})."
            ),
        },
    },
    "required": ["object_name", "aggregations"],
}


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "create_skill": ToolDefinition(
        name="create_skill",
        description="Create or update one reusable prompt skill in the skills table.",
        input_schema=CREATE_SKILL_SCHEMA,
        handler=_create_skill,
    ),
    "delete_skill": ToolDefinition(
        name="delete_skill",
        description="Hard-delete a skill from the skills table by skill_id.",
        input_schema=DELETE_SKILL_SCHEMA,
        handler=_delete_skill,
    ),
    "list_skills": ToolDefinition(
        name="list_skills",
        description=(
            "List every skill in the skills table with its id, name, and debrief "
            "(full prompt content is not included; use a follow-up tool or ask the "
            "user before loading large content)."
        ),
        input_schema=LIST_SKILLS_SCHEMA,
        handler=_list_skills,
    ),
    "list_ontology_objects": ToolDefinition(
        name="list_ontology_objects",
        description=(
            "List all ontology objects with their tables, descriptions, and "
            "attribute summaries (catalog metadata only, no row data)."
        ),
        input_schema=LIST_ONTOLOGY_OBJECTS_SCHEMA,
        handler=_list_ontology_objects,
    ),
    "read_ontology_schema": ToolDefinition(
        name="read_ontology_schema",
        description=(
            "Return the catalog schema for a single ontology object: table, "
            "description, and attribute names/types/descriptions."
        ),
        input_schema=READ_ONTOLOGY_SCHEMA_SCHEMA,
        handler=_read_ontology_schema,
    ),
    "read_ontology_data": ToolDefinition(
        name="read_ontology_data",
        description=(
            "Read paginated rows from an allowlisted ontology table. Supports "
            "optional filters and order_by over catalog-defined columns; limit "
            "is clamped at 50."
        ),
        input_schema=READ_ONTOLOGY_DATA_SCHEMA,
        handler=_read_ontology_data,
    ),
    "aggregate_ontology_data": ToolDefinition(
        name="aggregate_ontology_data",
        description=(
            "Compute count/count_distinct/sum/avg/min/max over an allowlisted "
            "ontology table, optionally filtered and grouped. Use this for any "
            "totals/counts/averages question. Never compute numbers in your head."
        ),
        input_schema=AGGREGATE_ONTOLOGY_DATA_SCHEMA,
        handler=_aggregate_ontology_data,
    ),
}


def tool_schemas() -> list[dict[str, Any]]:
    return [tool.as_openai_tool() for tool in TOOL_REGISTRY.values()]


def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None:
        return {"ok": False, "error": f"Tool `{tool_name}` is not allowed."}
    try:
        return tool.handler(tool_input)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
