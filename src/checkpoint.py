"""
Build structured BI checkpoint + rolling text summary from answer payloads.
"""

from __future__ import annotations

from typing import Any

from .conversation_models import ChatThread


def build_structured_checkpoint(result: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic state for SQL-style replay (no LLM ambiguity).
    Mirrors router intents → analytics contract filters.
    """
    intent = result.get("intent", "UNKNOWN")
    base: dict[str, Any] = {
        "intent": intent,
        "metric": _intent_to_metric(intent),
        "dimensions": _intent_to_dimensions(intent),
        "filters": _intent_to_filters(intent),
        "routing_source": result.get("routing_source"),
        "confidence": result.get("confidence"),
    }
    return base


def _intent_to_metric(intent: str) -> str:
    return {
        "ENTERPRISE_Q1_VS_QUOTA": "segment_quota_attainment",
        "PIPELINE_OPEN_BEFORE_END_MARCH": "pipeline_value",
        "PIPELINE_DATE_NOT_SUPPORTED": "none",
        "REPS_AT_RISK_Q1": "rep_quota_gap",
        "IRONBRIDGE_LOSS_REASON": "deal_lookup",
        "QUOTA_ATTAINMENT_ALL_REPS_Q1": "rep_quota_attainment",
        "UNKNOWN": "none",
    }.get(intent, "none")


def _intent_to_dimensions(intent: str) -> list[str]:
    return {
        "ENTERPRISE_Q1_VS_QUOTA": ["segment", "rep"],
        "PIPELINE_OPEN_BEFORE_END_MARCH": ["stage", "close_date"],
        "PIPELINE_DATE_NOT_SUPPORTED": [],
        "REPS_AT_RISK_Q1": ["rep_id", "segment"],
        "IRONBRIDGE_LOSS_REASON": ["account_name", "deal_id"],
        "QUOTA_ATTAINMENT_ALL_REPS_Q1": ["rep_id", "segment"],
        "UNKNOWN": [],
    }.get(intent, [])


def _intent_to_filters(intent: str) -> list[dict[str, Any]]:
    q1 = {"field": "close_date", "op": "between", "value": ["2026-01-01", "2026-03-31"]}
    open_stages = ["Prospecting", "Discovery", "Proposal", "Negotiation"]
    if intent == "ENTERPRISE_Q1_VS_QUOTA":
        return [
            q1,
            {"field": "deals.segment", "op": "eq", "value": "Enterprise"},
            {"field": "stage", "op": "eq", "value": "Closed Won"},
        ]
    if intent == "PIPELINE_OPEN_BEFORE_END_MARCH":
        return [
            {"field": "stage", "op": "in", "value": open_stages},
            {"field": "close_date", "op": "<=", "value": "2026-03-31"},
        ]
    if intent == "PIPELINE_DATE_NOT_SUPPORTED":
        return [{"field": "note", "op": "eq", "value": "requested_close_window_not_implemented"}]
    if intent == "REPS_AT_RISK_Q1":
        return [q1, {"field": "stage", "op": "eq", "value": "Closed Won"}, {"field": "attainment", "op": "<", "value": 1.0}]
    if intent == "IRONBRIDGE_LOSS_REASON":
        return [{"field": "account_name", "op": "eq", "value": "Ironbridge"}]
    if intent == "QUOTA_ATTAINMENT_ALL_REPS_Q1":
        return [q1, {"field": "stage", "op": "eq", "value": "Closed Won"}]
    return []


def append_rolling_summary(thread: ChatThread, result: dict[str, Any], *, max_lines: int = 12) -> None:
    """Compress memory: one line per turn, newest last."""
    line = _summary_line(result)
    lines = [ln for ln in thread.summary_checkpoint.split("\n") if ln.strip()]
    lines.append(line)
    thread.summary_checkpoint = "\n".join(lines[-max_lines:])


def _summary_line(result: dict[str, Any]) -> str:
    intent = result.get("intent", "?")
    summary = (result.get("summary") or "").replace("\n", " ")
    if len(summary) > 160:
        summary = summary[:157] + "..."
    return f"[{intent}] {summary}"
