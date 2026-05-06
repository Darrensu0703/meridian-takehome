"""
Optional LLM parser for question routing.

The LLM is used only to classify phrasing into known intents.
All numbers are still computed by deterministic pandas code in metrics.py.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from .router import QuestionIntent

SUPPORTED_INTENTS = (
    "ENTERPRISE_Q1_VS_QUOTA",
    "PIPELINE_OPEN_BEFORE_END_MARCH",
    "PIPELINE_DATE_NOT_SUPPORTED",
    "REPS_AT_RISK_Q1",
    "IRONBRIDGE_LOSS_REASON",
    "QUOTA_ATTAINMENT_ALL_REPS_Q1",
    "UNKNOWN",
)


@dataclass(frozen=True)
class LLMRoute:
    intent: QuestionIntent
    confidence: float
    reasoning: str
    matched_phrases: tuple[str, ...]


def _coerce_confidence(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, v))


def _as_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return tuple(out)


def _safe_parse_intent(intent_name: Any) -> QuestionIntent:
    if not isinstance(intent_name, str):
        return QuestionIntent.UNKNOWN
    if intent_name not in SUPPORTED_INTENTS:
        return QuestionIntent.UNKNOWN
    return QuestionIntent[intent_name]


def parse_question_with_llm(
    question: str,
    *,
    conversation_context: str | None = None,
) -> LLMRoute | None:
    """
    Return LLM-parsed intent or None when LLM is unavailable.

    Availability rules:
      - MERIDIAN_ENABLE_LLM_ROUTER must be truthy (default: true)
      - OPENAI_API_KEY must be present
      - `openai` package must be installed
    """
    enabled = os.getenv("MERIDIAN_ENABLE_LLM_ROUTER", "1").strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return None
    if not os.getenv("OPENAI_API_KEY"):
        return None

    try:
        from openai import OpenAI  # type: ignore[reportMissingImports]
    except Exception:
        return None

    model = os.getenv("MERIDIAN_ROUTER_MODEL", "gpt-4o-mini")
    client = OpenAI()

    system_prompt = (
        "You are a strict intent classifier for a sales analytics demo.\n"
        "Return JSON only, no markdown.\n"
        "Choose exactly one intent from:\n"
        f"{', '.join(SUPPORTED_INTENTS)}\n"
        "Use UNKNOWN when uncertain.\n"
        "Do not produce any metrics or numeric answer.\n"
        "JSON schema:\n"
        '{'
        '"intent":"...",'
        '"confidence":0.0,'
        '"reasoning":"brief reason",'
        '"matched_phrases":["..."]'
        '}'
    )

    ctx_block = ""
    if conversation_context and conversation_context.strip():
        ctx_block = (
            "Conversation context (use only to resolve references like 'same segment', "
            "'that pipeline'; still output intent only, no numbers):\n"
            f"{conversation_context.strip()}\n\n"
        )

    user_prompt = (
        "Classify this question for routing (users may paraphrase; match semantic intent):\n"
        f"{ctx_block}"
        f"{question}\n\n"
        "Intent definitions:\n"
        "- ENTERPRISE_Q1_VS_QUOTA: Enterprise performance vs quota this quarter.\n"
        "- PIPELINE_OPEN_BEFORE_END_MARCH: open pipeline value with close <= 2026-03-31.\n"
        "- PIPELINE_DATE_NOT_SUPPORTED: user wants pipeline for another close window (e.g. Dec 2025, 2025) not implemented in this demo.\n"
        "- REPS_AT_RISK_Q1: reps below 100% Q1 quota attainment.\n"
        "- IRONBRIDGE_LOSS_REASON: why/loss reason for Ironbridge deal.\n"
        "- QUOTA_ATTAINMENT_ALL_REPS_Q1: generic rep-level Q1 quota/attainment request.\n"
        "- UNKNOWN: does not clearly map.\n"
    )

    try:
        resp = client.responses.create(
            model=model,
            temperature=0,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = (getattr(resp, "output_text", "") or "").strip()
        parsed = json.loads(raw)
    except Exception:
        return None

    intent = _safe_parse_intent(parsed.get("intent"))
    confidence = _coerce_confidence(parsed.get("confidence"))
    reasoning = str(parsed.get("reasoning", "")).strip()
    matched_phrases = _as_tuple(parsed.get("matched_phrases"))

    return LLMRoute(
        intent=intent,
        confidence=confidence,
        reasoning=reasoning,
        matched_phrases=matched_phrases,
    )
