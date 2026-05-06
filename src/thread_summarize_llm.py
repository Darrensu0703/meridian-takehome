"""
Optional OpenAI summarization for conversation checkpoint memory.

Summaries restate topics and scope only; authoritative numbers remain in the app + structured checkpoint.
"""

from __future__ import annotations

import json
import os
from .conversation_models import ChatMessage, ChatThread


def _enabled() -> bool:
    if os.getenv("OPENAI_API_KEY", "").strip() == "":
        return False
    v = os.getenv("MERIDIAN_ENABLE_LLM_SUMMARY", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _summary_model() -> str:
    return os.getenv("MERIDIAN_SUMMARY_MODEL", os.getenv("MERIDIAN_ROUTER_MODEL", "gpt-4o-mini"))


def build_transcript_for_summary(
    messages: list[ChatMessage],
    *,
    max_messages: int = 28,
    max_user_chars: int = 2500,
    max_assistant_chars: int = 2000,
) -> str:
    """Compact dialogue text for the summarizer (avoids dumping huge tables)."""
    lines: list[str] = []
    for m in messages[-max_messages:]:
        if m.role == "user":
            u = (m.content or "").strip()
            if len(u) > max_user_chars:
                u = u[: max_user_chars - 3] + "..."
            lines.append(f"User: {u}")
        elif m.role == "assistant":
            snap = m.result_snapshot or {}
            body = snap.get("summary") or (m.content or "")
            body = str(body).strip()
            if len(body) > max_assistant_chars:
                body = body[: max_assistant_chars - 3] + "..."
            lines.append(f"Assistant: {body}")
    return "\n".join(lines)


def summarize_thread_llm(thread: ChatThread) -> str | None:
    """
    Produce a compressed checkpoint summary for the thread.

    Returns None if disabled, missing API key, or the API call fails.
    """
    if not _enabled():
        return None

    try:
        from openai import OpenAI  # type: ignore[reportMissingImports]
    except Exception:
        return None

    transcript = build_transcript_for_summary(thread.messages)
    if not transcript.strip():
        return None

    prior = (thread.summary_checkpoint or "").strip()
    structured = thread.structured_checkpoint or {}

    system = (
        "You compress BI/analytics chat threads into a short checkpoint summary.\n"
        "Rules:\n"
        "- Do NOT invent revenue, pipeline dollars, quotas, or deal outcomes.\n"
        "- If numbers appear in the transcript, treat them as possibly stale; prefer saying "
        "'user asked about X; assistant answered per last turn' rather than restating figures.\n"
        "- Use the structured checkpoint JSON as the authoritative scope for metric/intent when present.\n"
        "- Output plain text: 3–8 bullet lines or two short paragraphs, max ~180 words.\n"
        "- Mention unresolved gaps explicitly if the user hit UNKNOWN intent or missing loss_reason.\n"
    )

    user_parts = [
        "Structured checkpoint (authoritative scope):\n"
        + json.dumps(structured, indent=2, ensure_ascii=False),
        "",
        "Prior summary (merge/refine; omit if empty):\n" + (prior or "(none)"),
        "",
        "Conversation transcript:\n" + transcript,
    ]
    user_content = "\n".join(user_parts)

    client = OpenAI()
    model = _summary_model()

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.2,
            max_tokens=450,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        return text if text else None
    except Exception:
        return None


def maybe_refresh_llm_summary(thread: ChatThread) -> bool:
    """
    If LLM summary is enabled and interval matches, replace ``thread.summary_checkpoint``.

    Returns True if a new summary was written.
    """
    if not _enabled():
        return False

    interval = max(1, _safe_int(os.getenv("MERIDIAN_SUMMARY_INTERVAL"), default=1))
    assistant_turns = sum(1 for m in thread.messages if m.role == "assistant")
    if assistant_turns == 0:
        return False
    if assistant_turns % interval != 0:
        return False

    text = summarize_thread_llm(thread)
    if not text:
        return False
    thread.summary_checkpoint = text
    return True


def _safe_int(raw: str | None, *, default: int) -> int:
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default
