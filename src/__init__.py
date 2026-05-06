"""Meridian take-home — metrics (`metrics.py`), routing (`router.py`), chat wiring (`answer.py`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .metrics import (
    DEFAULT_DATA_DIR,
    enterprise_segment_q1_attainment,
    ironbridge_deal,
    load_data,
    pipeline_open_deals,
    pipeline_value_before_end_of_march,
    quota_attainment_q1_by_rep,
    reps_at_risk_missing_q1_quota,
)
from .parser_llm import parse_question_with_llm

if TYPE_CHECKING:
    from .answer import answer_question as answer_question
    from .router import QuestionIntent as QuestionIntent
    from .router import RouteDecision as RouteDecision
    from .router import classify_question as classify_question

__all__ = [
    "DEFAULT_DATA_DIR",
    "answer_question",
    "classify_question",
    "enterprise_segment_q1_attainment",
    "format_answer_for_console",
    "ironbridge_deal",
    "load_data",
    "pipeline_open_deals",
    "pipeline_value_before_end_of_march",
    "parse_question_with_llm",
    "quota_attainment_q1_by_rep",
    "reps_at_risk_missing_q1_quota",
]


def __getattr__(name: str) -> Any:
    """Lazy imports so `python -m src.answer` does not load `answer` before runpy."""
    if name == "answer_question":
        from .answer import answer_question

        return answer_question
    if name == "format_answer_for_console":
        from .answer import format_answer_for_console

        return format_answer_for_console
    if name in ("classify_question", "QuestionIntent", "RouteDecision"):
        from . import router

        return getattr(router, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
