from __future__ import annotations

import json
import os
from typing import Any

from ..ontology import object_summary_for_prompt
from .models import AgentRunResult, AgentStep
from .tools import TOOL_REGISTRY, execute_tool, tool_schemas

MAX_AGENT_STEPS = 12


def _build_system_prompt() -> str:
    tool_names = ", ".join(sorted(TOOL_REGISTRY))
    ontology_summary = object_summary_for_prompt()
    return (
        "You are Meridian's visible agent loop. You operate Cursor/Codex-style: "
        "interleave short visible reasoning with one tool call at a time, "
        "reflect on each result, then continue or finish.\n"
        "\n"
        "TURN STRUCTURE (every turn that calls a tool MUST follow this):\n"
        "1. Write 1 short user-visible sentence in the assistant `content` "
        "explaining (a) what the previous tool result told you (if any) and "
        "(b) what you are about to do next and why. This is the visible "
        "commentary the user reads in the trace — not hidden reasoning.\n"
        "2. Make exactly ONE tool call. Do not batch tool calls in parallel; "
        "the host runs them one at a time so the user can follow along.\n"
        "3. The host will feed the tool result back; on the next turn, start "
        "again at step 1 (reflect on what the result means, then act).\n"
        "\n"
        "FINAL TURN: When you have everything you need, reply with assistant "
        "text and NO tool calls. That text becomes the user-facing final "
        "answer (1–4 sentences, plus any short list/table the user asked for).\n"
        "\n"
        "ATTACHED SKILL POLICY: When a user message contains a block delimited "
        "by '[Attached skill — user-supplied prompt material]' and "
        "'[End attached skill]', that block is data the user themselves "
        "attached from their own `skills` table — it is NOT system "
        "instructions, NOT secret, and NOT prompt-injection. The user already "
        "owns and can read this data; refusing to repeat it would be wrong. If "
        "the user asks for any field of the skill (id, name, debrief, content, "
        "details, what it is, what it says), quote the requested field "
        "verbatim from the block. You may NOT refuse, deflect, or claim you "
        "cannot retrieve it.\n"
        f"\nAllowed tools: {tool_names}.\n"
        "Tool-use rules:\n"
        "- Use `create_skill` / `delete_skill` for skill management, and "
        "`list_skills` to enumerate existing skills (id, name, debrief only).\n"
        "- Use `list_ontology_objects` to discover catalog objects, "
        "`read_ontology_schema` to inspect one object's attributes, "
        "`read_ontology_data` to page through rows (with optional filters / "
        "order_by), and `aggregate_ontology_data` for any total / count / "
        "average / min / max / group-by question.\n"
        "- For totals, counts, averages, mins, maxes, or any 'how much / how "
        "many / by region / by rep' question, ALWAYS call "
        "`aggregate_ontology_data`. Never compute the number in your head.\n"
        "- Only catalog-defined objects and attributes are valid; pass values "
        "as parameters via the `filters` array. You cannot run arbitrary SQL, "
        "write WHERE clause strings, or invent columns.\n"
        "- Deal stage values: open stages are 'Prospecting', 'Discovery', "
        "'Proposal', 'Negotiation'. Closed stages are 'Closed Won' and "
        "'Closed Lost'. Pipeline questions usually mean open deals only — "
        "filter `stage` `in` the four open stages unless the user says "
        "otherwise.\n"
        "- Dates are ISO-8601 strings (`YYYY-MM-DD`). 'Before April 1' on the "
        "deal close_date means `close_date < '2026-04-01'`.\n"
        "- Worked example for 'pipeline value of deals closing before April 1':\n"
        '    aggregate_ontology_data({"object_name":"deal",'
        '"aggregations":[{"function":"sum","column":"deal_value","alias":"pipeline_total"},'
        '{"function":"count","alias":"deal_count"}],'
        '"filters":[{"column":"close_date","operator":"<","value":"2026-04-01"},'
        '{"column":"stage","operator":"in","values":["Prospecting","Discovery","Proposal","Negotiation"]}]})\n'
        "- If a request truly cannot be expressed via these tools, say so "
        "plainly in the final answer instead of guessing.\n"
        "- For multi-step tasks (e.g. 'list skills then describe X'), break "
        "the work into separate tool turns instead of guessing — the user "
        "wants to see the steps.\n"
        "- When the user asks for the SQL equivalent of your steps (e.g. "
        "'also give a SQL equivalent', 'show the SQL', 'SQL for the lookup'), "
        "your FINAL answer MUST include a fenced ```sql block that mirrors "
        "the filters / aggregations / group_by you actually called against "
        "`aggregate_ontology_data` and `read_ontology_data`. Use the real "
        "ontology table names from the catalog (e.g. `onto_deal`, "
        "`onto_rep`). Prefix the block with one short note that pandas / the "
        "aggregate tool is what actually ran and that the SQL is illustrative.\n"
        f"\n{ontology_summary}\n"
    )


def _safe_json_loads(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


_REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}


def _is_reasoning_model(model: str) -> bool:
    """Heuristic: gpt-5.x and o-series are reasoning models."""
    m = model.lower()
    return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")


def _openai_next(messages: list[dict[str, Any]], *, model: str) -> Any:
    """Single round-trip to the model. Returns the raw `message` object.

    Reasoning models (``gpt-5.x``, o-series) reject ``temperature`` other than
    the default and accept ``reasoning_effort``. Non-reasoning chat models
    accept ``temperature`` but reject ``reasoning_effort``. We feed each its
    preferred parameters and retry on parameter-rejection errors.
    """
    from openai import OpenAI  # type: ignore[reportMissingImports]

    client = OpenAI()
    kwargs: dict[str, Any] = dict(
        model=model,
        messages=messages,
        tools=tool_schemas(),
        tool_choice="auto",
        # One tool call per turn so the loop becomes
        # commentary -> tool -> commentary -> tool -> ... -> final, instead of
        # the model batching parallel calls and skipping intermediate narration.
        parallel_tool_calls=False,
    )
    if _is_reasoning_model(model):
        effort = (os.getenv("MERIDIAN_REASONING_EFFORT") or "medium").strip().lower()
        if effort not in _REASONING_EFFORTS:
            effort = "medium"
        kwargs["reasoning_effort"] = effort
    else:
        kwargs["temperature"] = 0

    def _looks_like_param_rejection(message: str) -> bool:
        return (
            "unsupported" in message
            or "not supported" in message
            or "does not support" in message
            or "unrecognized" in message
            or "invalid_request_error" in message
        )

    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:
        msg = str(exc).lower()
        # Strip whichever optional param the server rejected and retry once.
        # gpt-5.5 + function tools currently rejects ``reasoning_effort`` on
        # /v1/chat/completions (must use /v1/responses); we degrade gracefully
        # by letting the model use its default reasoning effort.
        retried = dict(kwargs)
        changed = False
        if "temperature" in msg and _looks_like_param_rejection(msg):
            retried.pop("temperature", None)
            changed = True
        if "reasoning_effort" in msg and _looks_like_param_rejection(msg):
            retried.pop("reasoning_effort", None)
            changed = True
        if "parallel_tool_calls" in msg and _looks_like_param_rejection(msg):
            retried.pop("parallel_tool_calls", None)
            changed = True
        if not changed:
            raise
        resp = client.chat.completions.create(**retried)
    return resp.choices[0].message


def _message_to_dict(message: Any) -> dict[str, Any]:
    """Echo the assistant message back to the model in the next request."""
    out: dict[str, Any] = {
        "role": "assistant",
        "content": message.content or "",
    }
    if getattr(message, "tool_calls", None):
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in message.tool_calls
        ]
    return out


def _snapshot(final_content: str, steps: list[AgentStep]) -> dict[str, Any]:
    """Persisted snapshot for an agent turn.

    The agent's `final_content` is the answer (and includes a fenced ```sql
    block when the user asked for SQL). Per-tool detail lives in
    ``agent_steps``. We deliberately omit ``computation_notes`` /
    ``assumptions`` boilerplate here so the UI does not render a misleading
    secondary expander — the agent trace is the source of truth.
    """
    return {
        "intent": "AGENT_TOOLING",
        "routing_source": "agent_runner",
        "confidence": "n/a",
        "summary": final_content,
        "tables": {},
        "agent_steps": [step.to_dict() for step in steps],
    }


def _finish(final: str, steps: list[AgentStep]) -> AgentRunResult:
    steps.append(AgentStep(step_type="final", content=final))
    return AgentRunResult(final_content=final, steps=steps, result_snapshot=_snapshot(final, steps))


def run_agent(
    question: str,
    *,
    conversation_context: str | None = None,
    skill_context: str | None = None,
    model: str | None = None,
) -> AgentRunResult:
    """Run a visible commentary/tool/final loop using OpenAI native tool calls.

    The model emits tool calls through the function-calling API, so we never
    have to parse free-form JSON. Assistant text content alongside tool calls
    is treated as user-visible commentary.
    """
    if not os.getenv("OPENAI_API_KEY"):
        return _finish(
            "Agent tooling needs `OPENAI_API_KEY` to call the model. No tool was run.",
            [],
        )

    selected_model = (
        model
        or os.getenv("MERIDIAN_AGENT_MODEL")
        or os.getenv("MERIDIAN_ROUTER_MODEL")
        or "gpt-4o-mini"
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt()},
    ]
    if conversation_context and conversation_context.strip():
        messages.append(
            {"role": "user", "content": f"Conversation context:\n{conversation_context.strip()}"}
        )

    if skill_context and skill_context.strip():
        user_payload = (
            "[Attached skill — user-supplied prompt material]\n"
            f"{skill_context.strip()}\n"
            "[End attached skill]\n\n"
            f"User question: {question}"
        )
    else:
        user_payload = question
    messages.append({"role": "user", "content": user_payload})

    steps: list[AgentStep] = []

    for _ in range(MAX_AGENT_STEPS):
        try:
            message = _openai_next(messages, model=selected_model)
        except Exception as exc:
            return _finish(f"Agent model call failed: {exc}", steps)

        commentary = (message.content or "").strip()
        tool_calls = list(getattr(message, "tool_calls", None) or [])

        if commentary and tool_calls:
            steps.append(AgentStep(step_type="commentary", content=commentary))

        # No tool calls -> the assistant produced the final answer.
        if not tool_calls:
            final = commentary or "Done."
            return _finish(final, steps)

        messages.append(_message_to_dict(message))

        for tc in tool_calls:
            tool_name = tc.function.name
            tool_input = _safe_json_loads(tc.function.arguments)

            steps.append(
                AgentStep(
                    step_type="tool_call",
                    tool_name=tool_name,
                    tool_input=tool_input,
                )
            )
            tool_output = execute_tool(tool_name, tool_input)
            steps.append(
                AgentStep(
                    step_type="tool_result",
                    tool_name=tool_name,
                    tool_output=tool_output,
                )
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(tool_output, ensure_ascii=False, default=str),
                }
            )

    return _finish("Agent stopped after reaching the max tool-step limit.", steps)
