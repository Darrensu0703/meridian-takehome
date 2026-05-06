"""
Maps free-text questions → which metric path to run.

This is intentionally simple (keywords + order of checks) so you can see how routing
works without an LLM. Production systems often use the same idea + richer NLP or an LLM
that returns structured JSON (intent + filters), then **your code** still calls metrics.

Parameters:
  • Most numbers come from ANALYTICS_CONTRACT.md via constants in `metrics.py`
    (Q1 dates, open stages). Those are **not** parsed from the question.
  • The router only decides **which function** to call and passes **fixed** kwargs
    defined here (e.g. pipeline “before end of March” → March 31 cutoff).
  • Later you can add regex or an LLM to fill parameters (e.g. segment = SMB).
"""

from __future__ import annotations

import re
from enum import Enum, auto
from typing import NamedTuple


class QuestionIntent(Enum):
    """High-level intent — one branch in `answer.py`."""

    ENTERPRISE_Q1_VS_QUOTA = auto()
    PIPELINE_OPEN_BEFORE_END_MARCH = auto()
    PIPELINE_DATE_NOT_SUPPORTED = auto()  # e.g. Dec 2025 / 2025 — not implemented
    REPS_AT_RISK_Q1 = auto()
    IRONBRIDGE_LOSS_REASON = auto()
    QUOTA_ATTAINMENT_ALL_REPS_Q1 = auto()
    UNKNOWN = auto()


class RouteDecision(NamedTuple):
    intent: QuestionIntent
    """Which calculation branch to run."""

    matched_keywords: tuple[str, ...]
    """Which phrases fired — useful for debugging / UI (“you asked about pipeline”)."""

    notes: str
    """Human-readable note about parameters (defaults from contract)."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def classify_question(question: str) -> RouteDecision:
    """
    Decide intent from keywords. **Order matters**: first match wins.

    Extend this table as you add questions — no need for dozens of functions;
    many questions share the same metric with different wording.
    """
    q = _norm(question)

    # --- 1) Named account / “why did we lose …” ---------------------------------
    if "ironbridge" in q:
        return RouteDecision(
            QuestionIntent.IRONBRIDGE_LOSS_REASON,
            ("ironbridge",),
            "Lookup deal rows for account Ironbridge; loss_reason may be blank per contract.",
        )

    # --- 2) Enterprise segment vs quota (PDF example) ---------------------------
    if ("enterprise" in q or "enterprise segment" in q) and any(
        w in q for w in ("quota", "tracking", "attainment", "against", "vs", "versus")
    ):
        return RouteDecision(
            QuestionIntent.ENTERPRISE_Q1_VS_QUOTA,
            ("enterprise", "quota/tracking"),
            "Uses Q1 2026 Closed Won on deals.segment==Enterprise vs Enterprise reps' quota sum.",
        )

    # --- 3) Pipeline closing before end of March --------------------------------
    if "pipeline" in q and any(
        w in q for w in ("march", "end of march", "before march", "march 31", "march 31st")
    ):
        return RouteDecision(
            QuestionIntent.PIPELINE_OPEN_BEFORE_END_MARCH,
            ("pipeline", "march"),
            "Open stages only; sum deal_value where close_date <= 2026-03-31 (contract example).",
        )

    # --- 3b) Pipeline + close window outside the built-in March 2026 metric -----
    if "pipeline" in q and (
        re.search(r"\b2025\b", q)
        or re.search(r"\bdecember\b|\bdec\b", q)
        or re.search(r"end of\s+december|before.*\bdec\b|closing.*\bdec\b", q)
    ):
        return RouteDecision(
            QuestionIntent.PIPELINE_DATE_NOT_SUPPORTED,
            ("pipeline", "unsupported close window"),
            "Prototype implements only open pipeline with close_date <= 2026-03-31; Dec 2025 / 2025 is out of scope.",
        )

    # --- 4) Reps at risk / missing Q1 -------------------------------------------
    if any(
        phrase in q
        for phrase in (
            "at risk",
            "missing q1",
            "miss q1",
            "behind quota",
            "behind on quota",
            "not going to make",
        )
    ) or ("reps" in q and "risk" in q):
        return RouteDecision(
            QuestionIntent.REPS_AT_RISK_Q1,
            ("at risk", "reps"),
            "Reps with Q1 attainment < 100% (booked Closed Won in Q1 vs quota_q1_2026).",
        )

    # --- 5) Generic quota / attainment table ------------------------------------
    if any(
        w in q
        for w in (
            "quota",
            "attainment",
            "booking",
            "booked",
            "how much did we close",
            "closed won",
        )
    ):
        return RouteDecision(
            QuestionIntent.QUOTA_ATTAINMENT_ALL_REPS_Q1,
            ("quota",),
            "Full rep-level Q1 Closed Won vs quota table.",
        )

    return RouteDecision(
        QuestionIntent.UNKNOWN,
        (),
        "No keyword pattern matched; extend router.KEYWORDS or ask more specifically.",
    )
