"""
Meridian take-home — conversational BI chat (Streamlit).

Run from folder `meridian-takehome`:
  streamlit run streamlit_app.py

Persistence: JSON file under data/chat_store/ by default.
Set DATABASE_URL to use PostgreSQL (schema applied automatically).
"""

from __future__ import annotations

import inspect
import os
import re
import textwrap
from pathlib import Path

import pandas as pd
import streamlit as st

from src.agent import run_agent
from src.answer import answer_question
from src.checkpoint import append_rolling_summary, build_structured_checkpoint
from src.thread_summarize_llm import maybe_refresh_llm_summary
from src.conversation_models import ChatMessage, ChatThread, new_thread, utc_now_iso
from src.metrics import load_data
from src.result_serialize import serialize_result_for_storage
from src.skills import PostgresSkillStore, Skill, SkillSummary
from src.store import get_conversation_store
from src.store.factory import peek_conversation_store_error
from src.store.postgres_store import resolve_postgres_dsn
from src.term_explain import extract_definition_term
from src.explain_llm import explain_term_with_llm, format_term_explanation_md


def _describe_postgres_target() -> str | None:
    """Human-readable Postgres target (no secrets) for sidebar debugging."""
    try:
        import psycopg  # type: ignore[reportMissingImports]

        dsn = resolve_postgres_dsn(os.getenv("DATABASE_URL"))
        if not dsn:
            return None
        info = psycopg.conninfo.conninfo_to_dict(dsn)
        host = info.get("host") or ""
        port = info.get("port") or ""
        user = info.get("user") or ""
        dbname = info.get("dbname") or ""
        if not host:
            return None
        return f"`{user}@{host}:{port}/{dbname}`"
    except Exception:
        return None


def _maybe_load_dotenv() -> None:
    """
    Load local `.env` (project-local secrets) if present.

    This is optional: if python-dotenv isn't installed, we silently skip.
    """
    try:
        from dotenv import load_dotenv  # type: ignore[reportMissingImports]

        env_path = Path(__file__).resolve().parent / ".env"
        # Always load from the project folder (not the process CWD), so launching
        # Streamlit from another directory still picks up DATABASE_URL, etc.
        # We always prefer the project-local `.env` for this demo.
        # This avoids confusion from stale Windows/User env vars.
        load_dotenv(dotenv_path=env_path, override=True)
    except Exception:
        return


@st.cache_data
def _cached_load():
    return load_data()


def _call_answer_question(
    question: str,
    deals: pd.DataFrame,
    reps: pd.DataFrame,
    *,
    structured_checkpoint: dict | None,
    conversation_tail: str | None,
):
    """
    Call `answer_question` with conversation kwargs when supported (current `src/answer.py`).

    If an older cached `src.answer` is loaded (stale .pyc), fall back to the 3-arg form so
    the UI still runs; restart Streamlit after `pip install` / file edits to pick up changes.
    """
    params = inspect.signature(answer_question).parameters
    if "structured_checkpoint" in params and "conversation_tail" in params:
        return answer_question(
            question,
            deals,
            reps,
            structured_checkpoint=structured_checkpoint,
            conversation_tail=conversation_tail,
        )
    return answer_question(question, deals, reps)


def _format_tail(messages: list[ChatMessage], *, max_turns: int = 6) -> str:
    """Last N user/assistant pairs as plain text for LLM context."""
    lines: list[str] = []
    for m in messages[-max_turns * 2 :]:
        label = "User" if m.role == "user" else "Assistant"
        snippet = (m.content or "").strip()
        if len(snippet) > 500:
            snippet = snippet[:497] + "..."
        lines.append(f"{label}: {snippet}")
    return "\n".join(lines)


def _assistant_markdown_body(result_snapshot: dict) -> str:
    parts = [
        result_snapshot.get("summary") or "",
        "",
        f"*{result_snapshot.get('assumptions', '')}*",
        "",
        f"`Intent: {result_snapshot.get('intent')}` · "
        f"`Confidence: {result_snapshot.get('confidence')}` · "
        f"`Route: {result_snapshot.get('routing_source', 'unknown')}`",
    ]
    rd = result_snapshot.get("routing_detail")
    if rd:
        parts.extend(["", f"*{rd}*"])
    if result_snapshot.get("matched_keywords"):
        parts.append(
            "Matched: " + ", ".join(result_snapshot["matched_keywords"])
        )
    return "\n".join(parts)


def _render_agent_steps(agent_steps: list[dict]) -> None:
    if not agent_steps:
        return
    with st.expander("Agent trace", expanded=False):
        for i, step in enumerate(agent_steps, start=1):
            step_type = step.get("step_type", "unknown")
            if step_type == "commentary":
                st.markdown(f"**{i}. Commentary**")
                st.markdown(step.get("content") or "")
            elif step_type == "tool_call":
                st.markdown(f"**{i}. Tool call:** `{step.get('tool_name')}`")
                st.json(step.get("tool_input") or {})
            elif step_type == "tool_result":
                st.markdown(f"**{i}. Tool result:** `{step.get('tool_name')}`")
                st.json(step.get("tool_output") or {})
            elif step_type == "final":
                st.markdown(f"**{i}. Final**")
                st.markdown(step.get("content") or "")
            else:
                st.markdown(f"**{i}. {step_type}**")
                st.json(step)


def _render_assistant_message(content: str, snapshot: dict | None, agent_steps: list[dict] | None = None) -> None:
    """Render an assistant turn.

    The service is fully agentic: the agent's final text (which is expected to
    include a fenced ```sql block when the user asks for one) is the answer.
    We expose the per-step trace via the Agent trace expander; we no longer
    render a separate 'Logic & illustrative SQL' expander because the trace
    already shows every tool call's filters and aggregations.
    """
    st.markdown(content)
    _render_agent_steps(agent_steps or [])
    if not snapshot:
        return
    tables = snapshot.get("tables") or {}
    if tables:
        with st.expander("Row-level preview (optional)", expanded=False):
            for name, rows in tables.items():
                st.markdown(f"**{name}**")
                if rows:
                    st.dataframe(
                        pd.DataFrame(rows), width="stretch", hide_index=True
                    )
                else:
                    st.caption("(no rows)")


def _ensure_session() -> None:
    _maybe_load_dotenv()
    dsn = resolve_postgres_dsn(os.getenv("DATABASE_URL")) or ""
    prev_dsn = st.session_state.get("_conversation_store_dsn", "")
    if "conversation_store" not in st.session_state or prev_dsn != dsn:
        with st.spinner("Connecting to chat store (Postgres → JSON fallback)…"):
            st.session_state.conversation_store = get_conversation_store()
        st.session_state._conversation_store_dsn = dsn
        err = peek_conversation_store_error()
        st.session_state._conversation_store_error = err if err else None
    if "active_thread_id" not in st.session_state:
        store = st.session_state.conversation_store
        summaries = store.list_thread_summaries()
        if summaries:
            st.session_state.active_thread_id = summaries[0].id
        else:
            t = new_thread()
            t.updated_at = utc_now_iso()
            store.upsert_thread(t)
            st.session_state.active_thread_id = t.id


def _get_active_thread() -> ChatThread:
    store = st.session_state.conversation_store
    tid = st.session_state.active_thread_id
    t = store.get_thread(tid)
    if t is None:
        t = new_thread()
        t.updated_at = utc_now_iso()
        store.upsert_thread(t)
        st.session_state.active_thread_id = t.id
    return t


def _save_thread(thread: ChatThread) -> None:
    thread.updated_at = utc_now_iso()
    st.session_state.conversation_store.upsert_thread(thread)


SKILL_TOKEN_RE = re.compile(r"^/([A-Za-z0-9_\-]+)\s*", re.UNICODE)


def _pending_skill_token() -> str | None:
    """Return the slash-token (e.g. ``/teptestskill``) attached to the next msg, or None."""
    return st.session_state.get("pending_skill_token")


def _set_pending_skill_token(token: str | None) -> None:
    if token:
        st.session_state.pending_skill_token = token
    else:
        st.session_state.pop("pending_skill_token", None)


def _parse_skill_token(text: str) -> tuple[str | None, str]:
    """Parse a leading ``/<id>`` from a chat message.

    Returns ``(skill_id, remainder)`` where ``remainder`` is the message with
    the token (and any following whitespace) stripped. If no token is present,
    returns ``(None, text)`` unchanged.
    """
    m = SKILL_TOKEN_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def _resolve_skill_by_token(token_id: str) -> tuple[Skill | None, str | None]:
    """Resolve ``/<token>`` to a Skill row. Tries skill_id, then skill_name (case-insensitive)."""
    store, err = _get_skill_store()
    if err or store is None:
        return None, err or "Skill store unavailable."
    skill = store.get_skill(token_id)
    if skill is not None:
        return skill, None
    target = token_id.lower()
    for s in store.list_skills():
        if s.skill_name.lower() == target:
            full = store.get_skill(s.skill_id)
            if full is not None:
                return full, None
    return None, f"No skill matched `/{token_id}`."


def _get_skill_store() -> tuple[PostgresSkillStore | None, str | None]:
    cached = st.session_state.get("_skill_store_cache")
    if cached is not None:
        store, err = cached
        return store, err

    timeout_s = float(os.getenv("MERIDIAN_PG_INIT_TIMEOUT_SECONDS", "3"))
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTimeout

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(PostgresSkillStore)
        try:
            store = future.result(timeout=timeout_s)
        except _FTimeout:
            executor.shutdown(wait=False, cancel_futures=True)
            err = f"Postgres skill store init exceeded {timeout_s:g}s"
            st.session_state._skill_store_cache = (None, err)
            return None, err
        executor.shutdown(wait=False)
        st.session_state._skill_store_cache = (store, None)
        return store, None
    except Exception as exc:  # pragma: no cover - propagate any non-timeout error
        executor.shutdown(wait=False)
        err = str(exc)
        st.session_state._skill_store_cache = (None, err)
        return None, err


def _skill_label(skill: SkillSummary) -> str:
    debrief = skill.skill_debrief.strip()
    return f"{skill.skill_name} — {debrief}" if debrief else skill.skill_name


def _attach_skill_token(skill_id: str) -> None:
    """Mark ``/<skill_id>`` as the chip to prepend to the next user message."""
    _set_pending_skill_token(f"/{skill_id}")


def _render_pending_skill_chip(thread_id: str) -> None:
    """Show the attached skill chip above ``st.chat_input`` with a remove button."""
    token = _pending_skill_token()
    if not token:
        return
    cols = st.columns([5, 1])
    with cols[0]:
        st.info(
            f"Skill attached: `{token}` — will be prepended to your next message "
            "and resolved server-side into prompt instructions."
        )
    with cols[1]:
        if st.button("Remove", key=f"remove_skill_token_{thread_id}", width="stretch"):
            _set_pending_skill_token(None)
            st.rerun()


def _render_skill_popover(thread_id: str) -> None:
    """Always-visible 'Skills' button that opens a small floating panel.

    Clicking ``Use`` on a skill sets it as the pending chip; on the next chat
    submit the chip's ``/<skill_id>`` token is prepended to the message and
    parsed by the backend to inject ``skill_content`` into ``run_agent``.
    """
    pending = _pending_skill_token()
    label = f"Skill: {pending}" if pending else "Skills"
    with st.popover(label, use_container_width=False):
        skill_store, err = _get_skill_store()
        if err or skill_store is None:
            st.warning(f"Could not load skills: {err or 'skill store unavailable'}")
            return

        skills = skill_store.list_skills()
        if not skills:
            st.info("No skills available yet. Add rows to the `skills` table to populate this list.")
            return

        st.caption("Click `Use` to attach `/<skill_id>` to your next message.")
        for s in skills:
            with st.container(border=True):
                st.markdown(f"**{s.skill_name}**  &nbsp; `/{s.skill_id}`", unsafe_allow_html=True)
                if s.skill_debrief:
                    st.caption(s.skill_debrief)
                is_attached = pending == f"/{s.skill_id}"
                if st.button(
                    "Attached" if is_attached else "Use",
                    key=f"popover_use_{thread_id}_{s.skill_id}",
                    type="secondary" if is_attached else "primary",
                    disabled=is_attached,
                    width="stretch",
                ):
                    _attach_skill_token(s.skill_id)
                    st.rerun()


@st.dialog("Select a skill")
def _skill_picker_dialog(thread_id: str) -> None:
    query = str(st.session_state.get("skill_picker_query") or "").strip().lower()
    skill_store, err = _get_skill_store()

    if err or skill_store is None:
        st.warning(f"Could not load skills: {err or 'skill store unavailable'}")
        if st.button("Close", key=f"close_skill_picker_{thread_id}", width="stretch"):
            st.session_state.show_skill_picker = False
            st.rerun()
        return

    skills = skill_store.list_skills()
    if query:
        skills = [
            s
            for s in skills
            if query in s.skill_id.lower()
            or query in s.skill_name.lower()
            or query in s.skill_debrief.lower()
        ]

    if not skills:
        if query:
            st.info(f"No skills matched `/{query}`.")
        else:
            st.info("No skills available yet. Add rows to the `skills` table to populate this list.")
        if st.button("Close", key=f"close_empty_skill_picker_{thread_id}", width="stretch"):
            st.session_state.show_skill_picker = False
            st.session_state.skill_picker_query = ""
            st.rerun()
        return

    st.caption("Selecting a skill attaches `/<skill_id>` to your next message.")
    idx = st.radio(
        "Available skills",
        range(len(skills)),
        format_func=lambda i: _skill_label(skills[i]),
        key=f"skill_picker_radio_{thread_id}",
        label_visibility="collapsed",
    )
    selected = skills[idx]
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Attach skill", key=f"use_skill_{thread_id}", width="stretch", type="primary"):
            _attach_skill_token(selected.skill_id)
            st.session_state.show_skill_picker = False
            st.session_state.skill_picker_query = ""
            st.rerun()
    with col2:
        if st.button("Cancel", key=f"cancel_skill_picker_{thread_id}", width="stretch"):
            st.session_state.show_skill_picker = False
            st.session_state.skill_picker_query = ""
            st.rerun()


def _render_skill_picker(thread_id: str) -> None:
    if st.session_state.get("show_skill_picker"):
        _skill_picker_dialog(thread_id)


def main() -> None:
    _maybe_load_dotenv()
    st.set_page_config(page_title="Meridian Q&A", layout="wide")
    _ensure_session()

    store = st.session_state.conversation_store

    with st.sidebar:
        st.header("Conversations")
        st.caption(
            "Full history + rolling summary + structured checkpoint. "
            "Default: JSON file in `data/chat_store/`. Set `DATABASE_URL` for Postgres."
        )
        store_kind = type(store).__name__
        if store_kind == "PostgresConversationStore":
            st.caption("Storage: **PostgreSQL** (`chat_threads`, `chat_messages`)")
        else:
            st.caption("Storage: **JSON file** (`data/chat_store/threads.json`)")
            st.caption(
                "Tip: set `MERIDIAN_PG_HOST`/`MERIDIAN_PG_PASSWORD` (or `DATABASE_URL`) in `.env` to enable Postgres chat persistence."
            )
        pg_target = _describe_postgres_target()
        if pg_target:
            st.caption(f"Postgres target: {pg_target}")
        st.caption(
            "Env: "
            f"`MERIDIAN_PG_HOST={'set' if os.getenv('MERIDIAN_PG_HOST') else 'unset'}` · "
            f"`MERIDIAN_PG_PASSWORD={'set' if os.getenv('MERIDIAN_PG_PASSWORD') else 'unset'}` · "
            f"`DATABASE_URL={'set' if os.getenv('DATABASE_URL') else 'unset'}` · "
            f"`OPENAI_API_KEY={'set' if os.getenv('OPENAI_API_KEY') else 'unset'}`"
        )
        err = st.session_state.get("_conversation_store_error")
        if err:
            st.warning(
                "Postgres chat persistence failed — using JSON instead.\n\n"
                f"Details: `{err}`"
            )
        if st.button("+ New chat", width="stretch"):
            t = new_thread()
            t.updated_at = utc_now_iso()
            store.upsert_thread(t)
            st.session_state.active_thread_id = t.id
            st.rerun()

        summaries = store.list_thread_summaries()
        options = {f"{x.title} · {x.updated_at[:19]}": x.id for x in summaries}
        if not options:
            t = new_thread()
            store.upsert_thread(t)
            options = {f"{t.title} · {t.updated_at[:19]}": t.id}

        labels = list(options.keys())
        ids = list(options.values())
        try:
            idx = ids.index(st.session_state.active_thread_id)
        except ValueError:
            idx = 0
            st.session_state.active_thread_id = ids[0]

        choice = st.radio(
            "History",
            range(len(labels)),
            format_func=lambda i: labels[i],
            index=idx,
            label_visibility="collapsed",
        )
        chosen_id = ids[choice]
        if chosen_id != st.session_state.active_thread_id:
            st.session_state.active_thread_id = chosen_id
            st.rerun()

        thread = store.get_thread(st.session_state.active_thread_id)
        if thread and st.button("Delete this thread", type="secondary"):
            store.delete_thread(thread.id)
            remaining = store.list_thread_summaries()
            if remaining:
                st.session_state.active_thread_id = remaining[0].id
            else:
                nt = new_thread()
                store.upsert_thread(nt)
                st.session_state.active_thread_id = nt.id
            st.rerun()

        if thread:
            with st.expander("Checkpoint — summary (compressed)"):
                st.text(
                    thread.summary_checkpoint
                    or "(Summary builds as you ask more questions.)"
                )
            with st.expander("Checkpoint — structured state (BI)"):
                st.json(thread.structured_checkpoint or {})

    thread = _get_active_thread()

    st.title(thread.title)
    st.caption(
        "Answers follow `ANALYTICS_CONTRACT.md` and CSV data. "
        "Optional LLM routing: set `OPENAI_API_KEY`. Type `/` to select a prompt skill."
    )

    for m in thread.messages:
        with st.chat_message(m.role):
            if m.role == "assistant" and m.result_snapshot:
                _render_assistant_message(m.content, m.result_snapshot, m.agent_steps)
            else:
                st.markdown(m.content)

    _render_skill_picker(thread.id)
    _render_pending_skill_chip(thread.id)
    _render_skill_popover(thread.id)

    prompt = st.chat_input(
        "Ask about pipeline, quota, Enterprise, Ironbridge…  (click 'Skills' or type / to attach a skill)"
    )
    if not prompt:
        return

    q = prompt.strip()
    if not q:
        return

    # Bare `/` (no name yet): open the picker dialog for keyboard users.
    if q == "/":
        st.session_state.show_skill_picker = True
        st.session_state.skill_picker_query = ""
        st.rerun()

    # If the user picked a skill from the popover, prepend its `/<id>` token
    # to the message before parsing. We then clear the chip so each message
    # decides fresh whether to attach.
    pending = _pending_skill_token()
    if pending and not q.startswith("/"):
        q = f"{pending} {q}".strip()
    _set_pending_skill_token(None)

    # Resolve `/<token>` at the start of the message into a real skill.
    token_id, remainder = _parse_skill_token(q)
    selected_skill: Skill | None = None
    selected_skill_error: str | None = None
    if token_id:
        selected_skill, selected_skill_error = _resolve_skill_by_token(token_id)
        if selected_skill_error:
            st.warning(selected_skill_error)

    # Stored user message keeps the literal `/<id>` prefix so it stays visible
    # in chat history; the agent receives only the question portion.
    user_message_for_history = q
    user_message_for_agent = remainder if (token_id and selected_skill) else q

    thread.messages.append(ChatMessage(role="user", content=user_message_for_history))

    last_assistant_snapshot = None
    for m in reversed(thread.messages[:-1]):
        if m.role == "assistant" and m.result_snapshot:
            last_assistant_snapshot = m.result_snapshot
            break

    # NOTE: legacy `is_followup_logic_request` short-circuit removed — the
    # service is fully agentic, so even "show me the SQL" follow-ups go
    # through `run_agent`, which can re-emit a SQL block from the prior
    # tool calls in conversation context.

    term = extract_definition_term(user_message_for_agent)
    if term and last_assistant_snapshot:
        explained = explain_term_with_llm(term, last_result_snapshot=last_assistant_snapshot)
        if explained is not None:
            thread.messages.append(
                ChatMessage(role="assistant", content=format_term_explanation_md(explained), result_snapshot=None)
            )
            if thread.title == "New chat":
                base = user_message_for_agent or q
                thread.title = (base[:48] + "…") if len(base) > 48 else base
            _save_thread(thread)
            st.rerun()

    tail = _format_tail(thread.messages[:-1])
    skill_payload: str | None = None
    if selected_skill is not None:
        skill_payload = (
            f"skill_id: {selected_skill.skill_id}\n"
            f"skill_name: {selected_skill.skill_name}\n"
            f"skill_debrief: {selected_skill.skill_debrief}\n"
            f"skill_content:\n{selected_skill.skill_content}"
        )
    with st.spinner("Agent is working (commentary + allowlisted tools)…"):
        agent_result = run_agent(
            user_message_for_agent,
            conversation_context=tail or None,
            skill_context=skill_payload,
        )

    result = agent_result.result_snapshot or {}
    if selected_skill:
        result["selected_skill"] = {
            "skill_id": selected_skill.skill_id,
            "skill_name": selected_skill.skill_name,
            "skill_debrief": selected_skill.skill_debrief,
        }
    snapshot = serialize_result_for_storage(result)
    body = agent_result.final_content
    agent_steps = [step.to_dict() for step in agent_result.steps]
    thread.messages.append(
        ChatMessage(
            role="assistant",
            content=body,
            result_snapshot=snapshot,
            agent_steps=agent_steps,
        )
    )

    thread.structured_checkpoint = build_structured_checkpoint(result)
    if not maybe_refresh_llm_summary(thread):
        append_rolling_summary(thread, result)

    if thread.title == "New chat":
        base = user_message_for_agent or q
        thread.title = (base[:48] + "…") if len(base) > 48 else base

    _save_thread(thread)
    st.rerun()


if __name__ == "__main__":
    main()
