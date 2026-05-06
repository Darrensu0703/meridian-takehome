"""Serialize pandas-heavy answer dicts for JSON / DB storage."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd


def _records_json_safe(df: pd.DataFrame, *, max_rows: int) -> list[dict[str, Any]]:
    """Round-trip via pandas JSON so Timestamp/datetime/numpy scalars become JSON-native."""
    sub = df.head(max_rows)
    if sub.empty:
        return []
    payload = sub.to_json(orient="records", date_format="iso", default_handler=str)
    return json.loads(payload)


def serialize_result_for_storage(result: dict[str, Any], *, table_rows_max: int = 100) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in result.items():
        if k == "tables":
            tables_out: dict[str, Any] = {}
            for name, df in (v or {}).items():
                if isinstance(df, pd.DataFrame):
                    tables_out[name] = _records_json_safe(df, max_rows=table_rows_max)
                else:
                    tables_out[name] = []
            out["tables"] = tables_out
        else:
            out[k] = _json_scalar(v)
    return out


def _json_scalar(obj: Any) -> Any:
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat() if hasattr(obj, "isoformat") else str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, np.generic):
        return obj.item()
    return obj
