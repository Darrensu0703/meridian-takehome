from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TermExplanation:
    term: str
    definition: str
    how_computed: list[str]
    used_in: list[str]
    caveats: list[str]
    confidence: float


def _contract_text() -> str:
    path = Path(__file__).resolve().parents[1] / "ANALYTICS_CONTRACT.md"
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def explain_term_with_llm(
    term: str,
    *,
    last_result_snapshot: dict[str, Any] | None,
) -> TermExplanation | None:
    """
    LLM-only definitional explanation with guardrails.

    Returns None if unavailable (no key / disabled / openai missing).
    """
    enabled = os.getenv("MERIDIAN_ENABLE_LLM_EXPLAIN", "1").strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return None
    if not os.getenv("OPENAI_API_KEY"):
        return None

    try:
        from openai import OpenAI  # type: ignore[reportMissingImports]
    except Exception:
        return None

    model = os.getenv("MERIDIAN_EXPLAIN_MODEL", os.getenv("MERIDIAN_ROUTER_MODEL", "gpt-4o-mini"))
    client = OpenAI()

    contract = _contract_text()
    snapshot = last_result_snapshot or {}
    ctx = {
        "intent": snapshot.get("intent"),
        "implementation": snapshot.get("implementation"),
        "computation_notes": snapshot.get("computation_notes"),
        "assumptions": snapshot.get("assumptions"),
    }

    system_prompt = (
        "You explain analytics TERMS for a sales analytics demo.\n"
        "Return JSON only, no markdown.\n"
        "Guardrails:\n"
        "- Do NOT compute or invent any numbers.\n"
        "- Do NOT claim SQL is executed; treat SQL blocks as reference only.\n"
        "- Only define the requested term as used in THIS demo, consistent with the analytics contract and context.\n"
        "- If the term is not defined by the contract/context, say so and set low confidence.\n"
        "JSON schema:\n"
        "{"
        "\"term\":\"...\","
        "\"definition\":\"1-3 sentences\","
        "\"how_computed\":[\"...\"],"
        "\"used_in\":[\"...\"],"
        "\"caveats\":[\"...\"],"
        "\"confidence\":0.0"
        "}"
    )

    user_prompt = (
        f"Term to explain: {term}\n\n"
        "Analytics contract:\n"
        f"{contract}\n\n"
        "Last answer context (may be empty):\n"
        + json.dumps(ctx, indent=2, ensure_ascii=False)
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

    def _as_list(v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for x in v:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
        return out

    try:
        return TermExplanation(
            term=str(parsed.get("term") or term).strip(),
            definition=str(parsed.get("definition") or "").strip(),
            how_computed=_as_list(parsed.get("how_computed")),
            used_in=_as_list(parsed.get("used_in")),
            caveats=_as_list(parsed.get("caveats")),
            confidence=float(parsed.get("confidence") or 0.0),
        )
    except Exception:
        return None


def format_term_explanation_md(x: TermExplanation) -> str:
    parts: list[str] = []
    parts.append(f"**{x.term}**")
    if x.definition:
        parts.append(x.definition)
    if x.how_computed:
        parts.append("")
        parts.append("**How it’s computed (demo definition)**")
        parts.extend([f"- {s}" for s in x.how_computed])
    if x.used_in:
        parts.append("")
        parts.append("**Used in**")
        parts.extend([f"- {s}" for s in x.used_in])
    if x.caveats:
        parts.append("")
        parts.append("**Caveats**")
        parts.extend([f"- {s}" for s in x.caveats])
    parts.append("")
    parts.append(f"`Confidence: {x.confidence:.2f}`")
    return "\n".join(parts).strip()

