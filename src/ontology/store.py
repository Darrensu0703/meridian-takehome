"""Postgres-only read access to the ontology layer (`onto_*` tables).

The store is intentionally narrow: agent tools should never construct arbitrary
SQL. Only catalog-defined objects and attributes are reachable, identifiers are
quoted from the catalog (not from user input), and row counts are clamped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..store.postgres_store import _apply_session_timeouts, resolve_postgres_dsn
from .catalog import (
    ONTOLOGY_CATALOG,
    OntologyObject,
    get_object,
    list_objects,
    schema_dict,
)
from .query import (
    OntologyQueryError,
    build_aggregate_sql,
    build_count_sql,
    build_select_sql,
    validate_aggregations,
    validate_filters,
    validate_group_by,
    validate_order_by,
)


MAX_READ_LIMIT = 50
DEFAULT_READ_LIMIT = 10


class OntologyError(ValueError):
    """Raised for catalog/allowlist violations (unknown object, bad column, etc.)."""


@dataclass(frozen=True)
class OntologyPage:
    object_name: str
    table_name: str
    columns: tuple[str, ...]
    offset: int
    limit: int
    rows: tuple[dict[str, Any], ...]
    total_rows: int


@dataclass(frozen=True)
class AggregatePage:
    object_name: str
    table_name: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    row_count: int


def _coerce_value(value: Any) -> Any:
    """Make a row value JSON-friendly without losing precision."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _resolve_columns(obj: OntologyObject, columns: list[str] | None) -> list[str]:
    """Validate requested columns against the catalog. Default to all attributes."""
    allowed = set(obj.attribute_names())
    if columns is None or len(columns) == 0:
        return list(obj.attribute_names())
    cleaned: list[str] = []
    for raw in columns:
        if not isinstance(raw, str):
            raise OntologyError(f"Column entry must be a string, got {type(raw).__name__}.")
        name = raw.strip()
        if name not in allowed:
            raise OntologyError(
                f"Column `{name}` is not part of ontology object `{obj.name}`. "
                f"Allowed columns: {sorted(allowed)}"
            )
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


def _clamp_pagination(offset: Any, limit: Any) -> tuple[int, int]:
    try:
        offset_int = int(offset) if offset is not None else 0
    except (TypeError, ValueError) as exc:
        raise OntologyError(f"`offset` must be an integer; got {offset!r}.") from exc
    try:
        limit_int = int(limit) if limit is not None else DEFAULT_READ_LIMIT
    except (TypeError, ValueError) as exc:
        raise OntologyError(f"`limit` must be an integer; got {limit!r}.") from exc
    if offset_int < 0:
        offset_int = 0
    if limit_int <= 0:
        limit_int = DEFAULT_READ_LIMIT
    if limit_int > MAX_READ_LIMIT:
        limit_int = MAX_READ_LIMIT
    return offset_int, limit_int


def _resolve_object(name: str) -> OntologyObject:
    obj = get_object(name)
    if obj is None:
        raise OntologyError(
            f"Unknown ontology object `{name}`. Allowed: {sorted(ONTOLOGY_CATALOG)}"
        )
    return obj


class PostgresOntologyStore:
    """Allowlisted read access to ontology tables in Postgres."""

    def __init__(self, dsn: str | None = None) -> None:
        import psycopg  # type: ignore[reportMissingImports]

        resolved_dsn = resolve_postgres_dsn(dsn)
        if not resolved_dsn:
            raise OntologyError(
                "PostgresOntologyStore requires DATABASE_URL or MERIDIAN_PG_* env vars."
            )
        self._psycopg = psycopg
        self._dsn = resolved_dsn

    def _connect(self):
        conn = self._psycopg.connect(self._dsn, autocommit=True)
        try:
            _apply_session_timeouts(conn)
        except Exception:
            conn.close()
            raise
        return conn

    def list_objects(self) -> list[dict[str, Any]]:
        """Return catalog metadata for every ontology object (no DB hit)."""
        return [schema_dict(obj) for obj in list_objects()]

    def get_schema(self, object_name: str) -> dict[str, Any]:
        return schema_dict(_resolve_object(object_name))

    def read_data(
        self,
        object_name: str,
        columns: list[str] | None = None,
        offset: int = 0,
        limit: int = DEFAULT_READ_LIMIT,
        filters: list[dict[str, Any]] | None = None,
        order_by: list[dict[str, Any]] | None = None,
    ) -> OntologyPage:
        obj = _resolve_object(object_name)
        resolved_cols = _resolve_columns(obj, columns)
        clamped_offset, clamped_limit = _clamp_pagination(offset, limit)

        try:
            filter_specs = validate_filters(obj, filters)
            order_specs = validate_order_by(order_by, allowed_columns=resolved_cols)
        except OntologyQueryError as exc:
            raise OntologyError(str(exc)) from exc

        count_sql, count_params = build_count_sql(obj, filter_specs)
        select_sql, select_params = build_select_sql(
            obj,
            columns=resolved_cols,
            filters=filter_specs,
            order_by=order_specs,
            limit=clamped_limit,
            offset=clamped_offset,
        )

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(count_sql, count_params)
                total_row = cur.fetchone()
                total_rows = int(total_row[0]) if total_row else 0

                cur.execute(select_sql, select_params)
                raw_rows = cur.fetchall()

        rows = tuple(
            {col: _coerce_value(value) for col, value in zip(resolved_cols, raw)}
            for raw in raw_rows
        )

        return OntologyPage(
            object_name=obj.name,
            table_name=obj.table_name,
            columns=tuple(resolved_cols),
            offset=clamped_offset,
            limit=clamped_limit,
            rows=rows,
            total_rows=total_rows,
        )

    def aggregate_data(
        self,
        object_name: str,
        *,
        aggregations: list[dict[str, Any]],
        filters: list[dict[str, Any]] | None = None,
        group_by: list[str] | None = None,
        order_by: list[dict[str, Any]] | None = None,
        limit: int = MAX_READ_LIMIT,
    ) -> AggregatePage:
        obj = _resolve_object(object_name)
        _, clamped_limit = _clamp_pagination(0, limit)

        try:
            agg_specs = validate_aggregations(obj, aggregations)
            filter_specs = validate_filters(obj, filters)
            group_cols = validate_group_by(obj, group_by)
            order_allowed = list(group_cols) + [a.alias for a in agg_specs]
            order_specs = validate_order_by(order_by, allowed_columns=order_allowed)
        except OntologyQueryError as exc:
            raise OntologyError(str(exc)) from exc

        sql, params, output_cols = build_aggregate_sql(
            obj,
            aggregations=agg_specs,
            filters=filter_specs,
            group_by=group_cols,
            order_by=order_specs,
            limit=clamped_limit,
        )

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                raw_rows = cur.fetchall()

        rows = tuple(
            {col: _coerce_value(value) for col, value in zip(output_cols, raw)}
            for raw in raw_rows
        )

        return AggregatePage(
            object_name=obj.name,
            table_name=obj.table_name,
            columns=tuple(output_cols),
            rows=rows,
            row_count=len(rows),
        )
