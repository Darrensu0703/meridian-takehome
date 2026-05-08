"""Validators + parameterized SQL builders for ontology filter / aggregate queries.

Everything in this module is catalog-driven: identifiers (table, columns,
group-by, order-by) are resolved against ``ONTOLOGY_CATALOG`` in
[catalog.py](catalog.py), and all *values* are passed to psycopg as parameters.
The agent never gets to inject identifiers or operators that aren't in the
allowlists below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from .catalog import OntologyAttribute, OntologyObject


# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

AGG_FUNCTIONS: dict[str, str] = {
    "count": "COUNT",
    "count_distinct": "COUNT",  # rendered as COUNT(DISTINCT ...) below
    "sum": "SUM",
    "avg": "AVG",
    "min": "MIN",
    "max": "MAX",
}

NUMERIC_ONLY_FUNCTIONS = {"sum", "avg"}

SCALAR_OPERATORS: dict[str, str] = {
    "=": "=",
    "!=": "<>",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
}

LIST_OPERATORS = {"in", "not_in"}
NULL_OPERATORS = {"is_null", "is_not_null"}

ALL_OPERATORS = (
    set(SCALAR_OPERATORS)
    | LIST_OPERATORS
    | NULL_OPERATORS
)

NUMERIC_TYPES = {"NUMERIC", "INTEGER", "INT", "FLOAT", "DOUBLE", "DECIMAL"}
DATE_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMPTZ"}

ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")

MAX_FILTERS = 8
MAX_AGGREGATIONS = 6
MAX_GROUP_BY = 3
MAX_ORDER_BY = 3


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OntologyQueryError(ValueError):
    """Raised when a filter/aggregation/order spec violates the catalog allowlist."""


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilterSpec:
    column: str
    operator: str
    value: Any = None
    values: tuple[Any, ...] | None = None
    attribute: OntologyAttribute | None = None


@dataclass(frozen=True)
class AggregationSpec:
    function: str
    column: str | None
    alias: str
    attribute: OntologyAttribute | None = None


@dataclass(frozen=True)
class OrderSpec:
    column: str
    direction: str  # "asc" or "desc"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _attribute_or_raise(obj: OntologyObject, column: str) -> OntologyAttribute:
    attr = obj.get_attribute(column)
    if attr is None:
        raise OntologyQueryError(
            f"Column `{column}` is not part of ontology object `{obj.name}`. "
            f"Allowed columns: {list(obj.attribute_names())}"
        )
    return attr


def _is_numeric_type(data_type: str) -> bool:
    return data_type.upper() in NUMERIC_TYPES


def _is_date_type(data_type: str) -> bool:
    return data_type.upper() in DATE_TYPES


def _coerce_numeric(value: Any, *, column: str) -> Any:
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly
        raise OntologyQueryError(f"Value for numeric column `{column}` must be a number, not bool.")
    if isinstance(value, (int, float, Decimal)):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise OntologyQueryError(
                f"Value `{value!r}` for numeric column `{column}` is not a number."
            ) from exc
    raise OntologyQueryError(f"Unsupported value type {type(value).__name__} for numeric column `{column}`.")


def _coerce_date(value: Any, *, column: str) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str) and DATE_RE.fullmatch(value):
        return value
    raise OntologyQueryError(
        f"Value `{value!r}` for date column `{column}` must be ISO-8601 (YYYY-MM-DD)."
    )


def _coerce_value(attr: OntologyAttribute, value: Any) -> Any:
    if _is_numeric_type(attr.data_type):
        return _coerce_numeric(value, column=attr.name)
    if _is_date_type(attr.data_type):
        return _coerce_date(value, column=attr.name)
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, str):
        return value
    raise OntologyQueryError(
        f"Unsupported value type {type(value).__name__} for column `{attr.name}`."
    )


def _column_sql_with_cast(attr: OntologyAttribute) -> str:
    """Quoted column reference; identifier always comes from the catalog."""
    return f'"{attr.name}"'


def _value_placeholder(attr: OntologyAttribute) -> str:
    """`%s` placeholder with an explicit cast so Postgres never has to guess."""
    if _is_date_type(attr.data_type):
        return "%s::date"
    if _is_numeric_type(attr.data_type):
        return "%s::numeric"
    return "%s"


def _list_placeholder(attr: OntologyAttribute) -> str:
    if _is_date_type(attr.data_type):
        return "%s::date[]"
    if _is_numeric_type(attr.data_type):
        return "%s::numeric[]"
    return "%s::text[]"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_filters(obj: OntologyObject, raw_filters: Any) -> list[FilterSpec]:
    if raw_filters is None:
        return []
    if not isinstance(raw_filters, list):
        raise OntologyQueryError("`filters` must be a list of objects.")
    if len(raw_filters) > MAX_FILTERS:
        raise OntologyQueryError(f"`filters` accepts at most {MAX_FILTERS} entries.")

    out: list[FilterSpec] = []
    for idx, entry in enumerate(raw_filters):
        if not isinstance(entry, dict):
            raise OntologyQueryError(f"`filters[{idx}]` must be an object.")
        column = entry.get("column")
        operator = entry.get("operator")
        if not isinstance(column, str) or not column.strip():
            raise OntologyQueryError(f"`filters[{idx}].column` is required and must be a string.")
        if not isinstance(operator, str) or operator not in ALL_OPERATORS:
            raise OntologyQueryError(
                f"`filters[{idx}].operator` must be one of {sorted(ALL_OPERATORS)}; got {operator!r}."
            )
        attr = _attribute_or_raise(obj, column.strip())

        if operator in NULL_OPERATORS:
            out.append(FilterSpec(column=attr.name, operator=operator, attribute=attr))
            continue

        if operator in LIST_OPERATORS:
            values = entry.get("values")
            if not isinstance(values, list) or len(values) == 0:
                raise OntologyQueryError(
                    f"`filters[{idx}].values` must be a non-empty list for operator `{operator}`."
                )
            coerced = tuple(_coerce_value(attr, v) for v in values)
            out.append(
                FilterSpec(column=attr.name, operator=operator, values=coerced, attribute=attr)
            )
            continue

        if "value" not in entry:
            raise OntologyQueryError(f"`filters[{idx}].value` is required for operator `{operator}`.")
        value = _coerce_value(attr, entry["value"])
        out.append(FilterSpec(column=attr.name, operator=operator, value=value, attribute=attr))
    return out


def _default_alias(function: str, column: str | None) -> str:
    base = function if column is None else f"{function}_{column}"
    return base[:40]


def validate_aggregations(obj: OntologyObject, raw_aggs: Any) -> list[AggregationSpec]:
    if not isinstance(raw_aggs, list) or len(raw_aggs) == 0:
        raise OntologyQueryError("`aggregations` must be a non-empty list.")
    if len(raw_aggs) > MAX_AGGREGATIONS:
        raise OntologyQueryError(f"`aggregations` accepts at most {MAX_AGGREGATIONS} entries.")

    out: list[AggregationSpec] = []
    seen_aliases: set[str] = set()
    for idx, entry in enumerate(raw_aggs):
        if not isinstance(entry, dict):
            raise OntologyQueryError(f"`aggregations[{idx}]` must be an object.")
        function = entry.get("function")
        if not isinstance(function, str) or function not in AGG_FUNCTIONS:
            raise OntologyQueryError(
                f"`aggregations[{idx}].function` must be one of {sorted(AGG_FUNCTIONS)}; got {function!r}."
            )

        raw_column = entry.get("column")
        attr: OntologyAttribute | None = None
        column_name: str | None = None
        if function == "count" and (raw_column is None or raw_column == ""):
            column_name = None
        else:
            if not isinstance(raw_column, str) or not raw_column.strip():
                raise OntologyQueryError(
                    f"`aggregations[{idx}].column` is required for function `{function}`."
                )
            attr = _attribute_or_raise(obj, raw_column.strip())
            column_name = attr.name
            if function in NUMERIC_ONLY_FUNCTIONS and not _is_numeric_type(attr.data_type):
                raise OntologyQueryError(
                    f"Function `{function}` requires a NUMERIC column; "
                    f"`{attr.name}` is `{attr.data_type}`."
                )

        raw_alias = entry.get("alias")
        if raw_alias is None or raw_alias == "":
            alias = _default_alias(function, column_name)
        else:
            if not isinstance(raw_alias, str) or not ALIAS_RE.fullmatch(raw_alias):
                raise OntologyQueryError(
                    f"`aggregations[{idx}].alias` must match {ALIAS_RE.pattern!r}; got {raw_alias!r}."
                )
            alias = raw_alias
        if alias in seen_aliases:
            raise OntologyQueryError(f"Duplicate aggregation alias `{alias}`.")
        seen_aliases.add(alias)

        out.append(AggregationSpec(function=function, column=column_name, alias=alias, attribute=attr))
    return out


def validate_group_by(obj: OntologyObject, raw_group_by: Any) -> list[str]:
    if raw_group_by is None:
        return []
    if not isinstance(raw_group_by, list):
        raise OntologyQueryError("`group_by` must be a list of column names.")
    if len(raw_group_by) > MAX_GROUP_BY:
        raise OntologyQueryError(f"`group_by` accepts at most {MAX_GROUP_BY} entries.")
    out: list[str] = []
    for raw in raw_group_by:
        if not isinstance(raw, str):
            raise OntologyQueryError("`group_by` entries must be strings.")
        attr = _attribute_or_raise(obj, raw.strip())
        if attr.name not in out:
            out.append(attr.name)
    return out


def validate_order_by(
    raw_order_by: Any,
    *,
    allowed_columns: Iterable[str],
) -> list[OrderSpec]:
    if raw_order_by is None:
        return []
    if not isinstance(raw_order_by, list):
        raise OntologyQueryError("`order_by` must be a list of objects.")
    if len(raw_order_by) > MAX_ORDER_BY:
        raise OntologyQueryError(f"`order_by` accepts at most {MAX_ORDER_BY} entries.")
    allowed = set(allowed_columns)
    out: list[OrderSpec] = []
    for idx, entry in enumerate(raw_order_by):
        if not isinstance(entry, dict):
            raise OntologyQueryError(f"`order_by[{idx}]` must be an object.")
        column = entry.get("column")
        if not isinstance(column, str) or column.strip() not in allowed:
            raise OntologyQueryError(
                f"`order_by[{idx}].column` must be one of {sorted(allowed)}; got {column!r}."
            )
        direction = (entry.get("direction") or "asc").strip().lower()
        if direction not in {"asc", "desc"}:
            raise OntologyQueryError(
                f"`order_by[{idx}].direction` must be 'asc' or 'desc'; got {direction!r}."
            )
        out.append(OrderSpec(column=column.strip(), direction=direction))
    return out


# ---------------------------------------------------------------------------
# SQL builders
# ---------------------------------------------------------------------------


def _build_where(filters: list[FilterSpec]) -> tuple[str, list[Any]]:
    if not filters:
        return "", []
    fragments: list[str] = []
    params: list[Any] = []
    for f in filters:
        attr = f.attribute
        assert attr is not None, "FilterSpec missing attribute (validation bug)"
        col_sql = _column_sql_with_cast(attr)
        if f.operator in NULL_OPERATORS:
            op_sql = "IS NULL" if f.operator == "is_null" else "IS NOT NULL"
            fragments.append(f"{col_sql} {op_sql}")
            continue
        if f.operator in LIST_OPERATORS:
            placeholder = _list_placeholder(attr)
            values_list = list(f.values or ())
            if f.operator == "in":
                fragments.append(f"{col_sql} = ANY({placeholder})")
            else:
                fragments.append(f"({col_sql} <> ALL({placeholder}) OR {col_sql} IS NULL)")
            params.append(values_list)
            continue
        sql_op = SCALAR_OPERATORS[f.operator]
        fragments.append(f"{col_sql} {sql_op} {_value_placeholder(attr)}")
        params.append(f.value)
    where_sql = "WHERE " + " AND ".join(fragments)
    return where_sql, params


def _agg_select_sql(spec: AggregationSpec) -> str:
    func_sql = AGG_FUNCTIONS[spec.function]
    if spec.function == "count" and spec.column is None:
        inner = "*"
    elif spec.function == "count_distinct":
        assert spec.attribute is not None
        inner = f'DISTINCT "{spec.attribute.name}"'
    else:
        assert spec.attribute is not None
        inner = f'"{spec.attribute.name}"'

    expression = f"{func_sql}({inner})"
    # Cast SUM/AVG to float so JSON serialization stays clean (psycopg returns
    # Decimal otherwise, and we already coerce Decimals -> float on the way out).
    if spec.function in {"sum", "avg"}:
        expression = f"{expression}::float"
    return f'{expression} AS "{spec.alias}"'


def _order_by_sql(order_by: list[OrderSpec]) -> str:
    if not order_by:
        return ""
    parts: list[str] = []
    for o in order_by:
        direction = "DESC" if o.direction == "desc" else "ASC"
        nulls = "NULLS LAST" if direction == "DESC" else "NULLS LAST"
        parts.append(f'"{o.column}" {direction} {nulls}')
    return "ORDER BY " + ", ".join(parts)


def build_aggregate_sql(
    obj: OntologyObject,
    *,
    aggregations: list[AggregationSpec],
    filters: list[FilterSpec],
    group_by: list[str],
    order_by: list[OrderSpec],
    limit: int,
) -> tuple[str, list[Any], list[str]]:
    """Returns ``(sql, params, output_columns)``."""
    table_sql = f'"{obj.table_name}"'
    select_parts = [_agg_select_sql(spec) for spec in aggregations]
    output_cols: list[str] = []
    if group_by:
        select_parts = [f'"{g}"' for g in group_by] + select_parts
        output_cols.extend(group_by)
    output_cols.extend(spec.alias for spec in aggregations)

    where_sql, where_params = _build_where(filters)
    group_sql = ("GROUP BY " + ", ".join(f'"{g}"' for g in group_by)) if group_by else ""
    order_sql = _order_by_sql(order_by)
    limit_sql = "LIMIT %s"

    sql = " ".join(
        part for part in (
            "SELECT " + ", ".join(select_parts),
            f"FROM {table_sql}",
            where_sql,
            group_sql,
            order_sql,
            limit_sql,
        )
        if part
    )
    params = list(where_params) + [int(limit)]
    return sql, params, output_cols


def build_select_sql(
    obj: OntologyObject,
    *,
    columns: list[str],
    filters: list[FilterSpec],
    order_by: list[OrderSpec],
    limit: int,
    offset: int,
) -> tuple[str, list[Any]]:
    table_sql = f'"{obj.table_name}"'
    cols_sql = ", ".join(f'"{c}"' for c in columns)
    where_sql, where_params = _build_where(filters)
    if order_by:
        order_sql = _order_by_sql(order_by)
    else:
        order_sql = f'ORDER BY "{columns[0]}" ASC'
    limit_sql = "LIMIT %s OFFSET %s"
    sql = " ".join(
        part for part in (
            f"SELECT {cols_sql}",
            f"FROM {table_sql}",
            where_sql,
            order_sql,
            limit_sql,
        )
        if part
    )
    params = list(where_params) + [int(limit), int(offset)]
    return sql, params


def build_count_sql(obj: OntologyObject, filters: list[FilterSpec]) -> tuple[str, list[Any]]:
    table_sql = f'"{obj.table_name}"'
    where_sql, where_params = _build_where(filters)
    sql = f"SELECT COUNT(*) FROM {table_sql} {where_sql}".strip()
    return sql, list(where_params)
