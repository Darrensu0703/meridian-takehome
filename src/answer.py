"""
Turn a user question into: summary text + trace tables + assumptions.

Flow:
  1) `classify_question` → intent (router.py)
  2) Call the right `metrics` functions — **numbers always from pandas**, not the LLM
  3) Return a dict your chat UI can render

Where parameters live:
  • Dates / stages / Enterprise definition → `metrics.py` + ANALYTICS_CONTRACT.md
  • Which formula runs → `router.py`
  • Extra kwargs (e.g. March 31 cutoff) → hard-coded in the match branch below OR
    parsed later from the question / LLM JSON.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from . import metrics as m
from .parser_llm import parse_question_with_llm
from .router import QuestionIntent, classify_question


def is_followup_logic_request(text: str) -> bool:
    """True if the user is asking how the last answer was computed / for SQL-like detail."""
    t = text.strip().lower()
    if len(t) > 240:
        return False
    keys = (
        "sql",
        "query",
        "underlying",
        "how was",
        "how did you",
        "explain the",
        "show the logic",
        "computation",
        "logic",
    )
    return any(k in t for k in keys)


def _conversation_context_for_llm(
    structured_checkpoint: dict[str, Any] | None,
    conversation_tail: str | None,
) -> str | None:
    parts: list[str] = []
    if structured_checkpoint:
        parts.append(
            "Structured checkpoint (authoritative):\n"
            + json.dumps(structured_checkpoint, indent=2, ensure_ascii=False)
        )
    if conversation_tail and conversation_tail.strip():
        parts.append("Recent turns:\n" + conversation_tail.strip())
    return "\n\n".join(parts) if parts else None


def answer_question(
    question: str,
    deals: pd.DataFrame,
    reps: pd.DataFrame,
    *,
    structured_checkpoint: dict[str, Any] | None = None,
    conversation_tail: str | None = None,
) -> dict:
    """
    Single entry point for the demo chat backend.

    Returns a plain dict so Streamlit/Gradio/CLI can display it without importing types.
    """
    route = classify_question(question)
    intent = route.intent
    routing_source = "keyword_router"

    llm_ctx = _conversation_context_for_llm(structured_checkpoint, conversation_tail)
    llm_route = parse_question_with_llm(question, conversation_context=llm_ctx)
    if llm_route is not None and llm_route.intent != QuestionIntent.UNKNOWN:
        intent = llm_route.intent
        routing_source = "llm_parser"
        route = route._replace(
            matched_keywords=llm_route.matched_phrases or route.matched_keywords,
            notes=f"LLM parser: {llm_route.reasoning or 'classified by semantic phrasing.'}",
        )
    assumptions = (
        "Assumptions follow ANALYTICS_CONTRACT.md: Q1 2026 = close_date between "
        "2026-01-01 and 2026-03-31; pipeline = open stages only unless stated otherwise."
    )

    # Default payload pieces
    summary_lines: list[str] = []
    tables: dict[str, pd.DataFrame] = {}
    confidence = "high"
    computation_notes = ""

    if intent == QuestionIntent.ENTERPRISE_Q1_VS_QUOTA:
        res = m.enterprise_segment_q1_attainment(deals, reps)
        pct = res["attainment"] * 100 if res["attainment"] == res["attainment"] else float("nan")
        summary_lines = [
            f"Enterprise Q1 2026 (Closed Won in window): ${res['booked_q1_closed_won']:,.0f} booked.",
            f"Sum of Q1 quotas for Enterprise reps: ${res['quota_sum_q1_reps']:,.0f}.",
            f"Attainment ≈ {pct:.1f}%." if pct == pct else "Attainment: n/a (quota sum zero).",
        ]
        tables["enterprise_closed_won_q1_deals"] = res["deal_rows"]
        tables["enterprise_reps_quotas"] = res["rep_rows"]
        computation_notes = (
            "Implementation: pandas in `metrics.enterprise_segment_q1_attainment()`.\n\n"
            "SQL (reference only — not executed by the app):\n"
            "-- Ontology tables (onto_*)\n"
            "SELECT SUM(d.deal_value) AS booked\n"
            "FROM onto_deal d\n"
            "JOIN onto_segment s ON s.segment_id = d.segment_id\n"
            "WHERE s.segment_name = 'Enterprise'\n"
            "  AND d.stage = 'Closed Won'\n"
            "  AND d.close_date BETWEEN DATE '2026-01-01' AND DATE '2026-03-31';\n\n"
            "SELECT SUM(q.quota) AS quota_sum\n"
            "FROM onto_rep_quota q\n"
            "JOIN onto_rep r ON r.rep_id = q.rep_id\n"
            "JOIN onto_segment s ON s.segment_id = r.segment_id\n"
            "WHERE q.period = '2026Q1' AND s.segment_name = 'Enterprise';\n\n"
            "-- Raw tables (deals/reps)\n"
            "-- Booked: Closed Won in Q1 2026, Enterprise deals\n"
            "SELECT SUM(deal_value) AS booked\n"
            "FROM deals\n"
            "WHERE segment = 'Enterprise'\n"
            "  AND stage = 'Closed Won'\n"
            "  AND close_date BETWEEN DATE '2026-01-01' AND DATE '2026-03-31';\n\n"
            "-- Quota denominator: Enterprise-book reps\n"
            "SELECT SUM(quota_q1_2026) AS quota_sum FROM reps WHERE segment = 'Enterprise';"
        )

    elif intent == QuestionIntent.PIPELINE_OPEN_BEFORE_END_MARCH:
        total, sub = m.pipeline_value_before_end_of_march(deals)
        summary_lines = [
            f"Total **open** pipeline value for deals with expected close on/before 2026-03-31: ${total:,.0f}.",
            f"({len(sub)} open-opportunity rows.)",
        ]
        tables["pipeline_open_close_on_or_before_march31"] = sub
        computation_notes = (
            "Implementation: pandas in `metrics.pipeline_open_deals(..., close_date_on_or_before=2026-03-31)`.\n\n"
            "SQL (reference only — not executed by the app):\n"
            "-- Ontology tables (onto_*)\n"
            "SELECT SUM(deal_value) AS pipeline_value\n"
            "FROM onto_deal\n"
            "WHERE stage IN ('Prospecting','Discovery','Proposal','Negotiation')\n"
            "  AND close_date <= DATE '2026-03-31';\n\n"
            "-- Raw tables (deals)\n"
            "SELECT SUM(deal_value) AS pipeline_value\n"
            "FROM deals\n"
            "WHERE stage IN ('Prospecting','Discovery','Proposal','Negotiation')\n"
            "  AND close_date <= DATE '2026-03-31';"
        )

    elif intent == QuestionIntent.PIPELINE_DATE_NOT_SUPPORTED:
        summary_lines = [
            "This prototype **does not** compute pipeline for that close-date window.",
            "The only built-in pipeline total is **open** opportunities (not Closed Won/Lost) with **expected `close_date` on or before 2026-03-31**, per `ANALYTICS_CONTRACT.md` and the sample assignment.",
            "A question about **December 2025** or **2025** is **outside** that definition, so we are **not** returning a dollar figure (that would look authoritative when the app has no matching metric).",
            "**Try instead:** *“What’s our total pipeline value for deals closing before end of March?”* (March **2026** in this demo.)",
        ]
        confidence = "high (out of scope)"
        computation_notes = (
            "No pandas aggregation: intent PIPELINE_DATE_NOT_SUPPORTED.\n"
            "Supported pipeline metric: open stages, close_date <= 2026-03-31 only."
        )

    elif intent == QuestionIntent.REPS_AT_RISK_Q1:
        at_risk = m.reps_at_risk_missing_q1_quota(deals, reps)
        summary_lines = [
            f"Reps under 100% Q1 attainment (Closed Won in Q1 vs quota): {len(at_risk)} rep(s).",
            "Interpret as “at risk” only with this definition — stated in analytics contract.",
        ]
        tables["reps_below_full_quota_q1"] = at_risk
        computation_notes = (
            "Implementation: pandas in `metrics.quota_attainment_q1_by_rep()` then filter `attainment < 1`.\n\n"
            "SQL (reference only — not executed by the app):\n"
            "-- Ontology tables (onto_*)\n"
            "WITH bookings AS (\n"
            "  SELECT rep_id, SUM(deal_value) AS booked_q1\n"
            "  FROM onto_deal\n"
            "  WHERE stage = 'Closed Won'\n"
            "    AND close_date BETWEEN DATE '2026-01-01' AND DATE '2026-03-31'\n"
            "  GROUP BY rep_id\n"
            ")\n"
            "SELECT r.rep_id, r.rep_name,\n"
            "       COALESCE(q.quota, 0) AS quota_q1,\n"
            "       COALESCE(b.booked_q1, 0) AS booked_q1,\n"
            "       COALESCE(b.booked_q1, 0) / NULLIF(q.quota, 0) AS attainment\n"
            "FROM onto_rep r\n"
            "LEFT JOIN bookings b ON r.rep_id = b.rep_id\n"
            "LEFT JOIN onto_rep_quota q ON q.rep_id = r.rep_id AND q.period = '2026Q1'\n"
            "WHERE COALESCE(b.booked_q1, 0) / NULLIF(q.quota, 0) < 1.0;\n\n"
            "-- Raw tables (deals/reps)\n"
            "WITH bookings AS (\n"
            "  SELECT rep_id, SUM(deal_value) AS booked_q1\n"
            "  FROM deals\n"
            "  WHERE stage = 'Closed Won'\n"
            "    AND close_date BETWEEN DATE '2026-01-01' AND DATE '2026-03-31'\n"
            "  GROUP BY rep_id\n"
            ")\n"
            "SELECT r.*, COALESCE(b.booked_q1, 0) AS booked_q1,\n"
            "       COALESCE(b.booked_q1, 0) / NULLIF(r.quota_q1_2026, 0) AS attainment\n"
            "FROM reps r\n"
            "LEFT JOIN bookings b ON r.rep_id = b.rep_id\n"
            "WHERE COALESCE(b.booked_q1, 0) / NULLIF(r.quota_q1_2026, 0) < 1.0;"
        )

    elif intent == QuestionIntent.IRONBRIDGE_LOSS_REASON:
        rows = m.ironbridge_deal(deals)
        if rows.empty:
            summary_lines = ["No deal row found for Ironbridge."]
            confidence = "high"
        else:
            lr = rows.iloc[0].get("loss_reason")
            summary_lines = [
                "Ironbridge is **Closed Lost** in the sample.",
                (
                    f"Recorded loss_reason: {lr!r}."
                    if pd.notna(lr) and str(lr).strip()
                    else "**loss_reason is blank** — we cannot answer “why” from this dataset without guessing."
                ),
            ]
            confidence = "high" if pd.notna(lr) and str(lr).strip() else "high (explicit gap)"
        tables["ironbridge_rows"] = rows
        computation_notes = (
            "Implementation: pandas filter `account_name == 'Ironbridge'` in `metrics.ironbridge_deal()`.\n\n"
            "SQL (reference only — not executed by the app):\n"
            "-- Ontology tables (onto_*)\n"
            "SELECT d.deal_id, d.stage, d.loss_reason\n"
            "FROM onto_deal d\n"
            "JOIN onto_account a ON a.account_id = d.account_id\n"
            "WHERE TRIM(a.account_name) = 'Ironbridge';\n\n"
            "-- Raw tables (deals)\n"
            "SELECT deal_id, stage, loss_reason\n"
            "FROM deals\n"
            "WHERE TRIM(account_name) = 'Ironbridge';"
        )

    elif intent == QuestionIntent.QUOTA_ATTAINMENT_ALL_REPS_Q1:
        tbl = m.quota_attainment_q1_by_rep(deals, reps)
        summary_lines = [
            "Q1 2026 attainment by rep: Closed Won sum in Q1 vs quota_q1_2026.",
            f"{len(tbl)} reps in roster.",
        ]
        tables["quota_attainment_q1_by_rep"] = tbl
        computation_notes = (
            "Implementation: pandas in `metrics.quota_attainment_q1_by_rep()`.\n\n"
            "SQL (reference only — not executed by the app):\n"
            "-- Ontology tables (onto_*)\n"
            "WITH bookings AS (\n"
            "  SELECT rep_id, SUM(deal_value) AS booked_q1\n"
            "  FROM onto_deal\n"
            "  WHERE stage = 'Closed Won'\n"
            "    AND close_date BETWEEN DATE '2026-01-01' AND DATE '2026-03-31'\n"
            "  GROUP BY rep_id\n"
            ")\n"
            "SELECT r.rep_id, r.rep_name,\n"
            "       COALESCE(q.quota, 0) AS quota_q1,\n"
            "       COALESCE(b.booked_q1, 0) AS booked_q1,\n"
            "       COALESCE(b.booked_q1, 0) / NULLIF(q.quota, 0) AS attainment\n"
            "FROM onto_rep r\n"
            "LEFT JOIN bookings b ON r.rep_id = b.rep_id\n"
            "LEFT JOIN onto_rep_quota q ON q.rep_id = r.rep_id AND q.period = '2026Q1';\n\n"
            "-- Raw tables (deals/reps)\n"
            "WITH bookings AS (\n"
            "  SELECT rep_id, SUM(deal_value) AS booked_q1\n"
            "  FROM deals\n"
            "  WHERE stage = 'Closed Won'\n"
            "    AND close_date BETWEEN DATE '2026-01-01' AND DATE '2026-03-31'\n"
            "  GROUP BY rep_id\n"
            ")\n"
            "SELECT r.*, COALESCE(b.booked_q1, 0) AS booked_q1,\n"
            "       COALESCE(b.booked_q1, 0) / NULLIF(r.quota_q1_2026, 0) AS attainment\n"
            "FROM reps r\n"
            "LEFT JOIN bookings b ON r.rep_id = b.rep_id;"
        )

    else:
        summary_lines = [
            "I didn’t match that question to a built-in metric yet.",
            "Try keywords like: Enterprise quota, pipeline March, reps at risk, Ironbridge, or quota.",
        ]
        confidence = "low"
        assumptions = route.notes
        computation_notes = "No computation: question did not match a routed metric."

    return {
        "intent": intent.name,
        "routing_source": routing_source,
        "matched_keywords": route.matched_keywords,
        "routing_notes": route.notes,
        "summary": "\n".join(summary_lines),
        "tables": tables,
        "assumptions": assumptions,
        "confidence": confidence,
        "computation_notes": computation_notes,
    }


def format_answer_for_console(result: dict) -> str:
    """Tiny helper for terminal demos."""
    parts = [
        f"[{result['intent']}] confidence={result['confidence']} route={result.get('routing_source', 'unknown')}",
        result["summary"],
        "",
        result["assumptions"],
    ]
    if result["matched_keywords"]:
        parts.insert(2, f"(matched: {', '.join(result['matched_keywords'])})")
    notes = result.get("computation_notes")
    if notes:
        parts.extend(["", "--- logic / illustrative SQL ---", notes])
    for name, df in result["tables"].items():
        parts.append("")
        parts.append(f"--- {name} ({len(df)} rows) ---")
        parts.append(df.head(20).to_string(index=False))
        if len(df) > 20:
            parts.append(f"... ({len(df) - 20} more rows)")
    return "\n".join(parts)


if __name__ == "__main__":
    import sys

    deals_df, reps_df = m.load_data()
    q = " ".join(sys.argv[1:]).strip() or "How is Enterprise tracking against quota this quarter?"
    print(format_answer_for_console(answer_question(q, deals_df, reps_df)))
