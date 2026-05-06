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
import textwrap
from pathlib import Path

import pandas as pd
import streamlit as st

from src.answer import answer_question, is_followup_logic_request
from src.checkpoint import append_rolling_summary, build_structured_checkpoint
from src.thread_summarize_llm import maybe_refresh_llm_summary
from src.conversation_models import ChatMessage, ChatThread, new_thread, utc_now_iso
from src.metrics import load_data
from src.result_serialize import serialize_result_for_storage
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
    if result_snapshot.get("matched_keywords"):
        parts.append(
            "Matched: " + ", ".join(result_snapshot["matched_keywords"])
        )
    return "\n".join(parts)


def _render_assistant_message(content: str, snapshot: dict | None) -> None:
    st.markdown(content)
    if not snapshot:
        return
    with st.expander("Logic & illustrative SQL (pandas is what actually runs)"):
        st.code(snapshot.get("computation_notes") or "", language="sql")
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
        st.session_state.conversation_store = get_conversation_store()
        st.session_state._conversation_store_dsn = dsn
        err = peek_conversation_store_error()
        st.session_state._conversation_store_error = err if err else None
    if "active_thread_id" not in st.session_state:
        store = st.session_state.conversation_store
        threads = store.list_threads()
        if threads:
            st.session_state.active_thread_id = threads[0].id
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


def main() -> None:
    _maybe_load_dotenv()
    st.set_page_config(page_title="Meridian Q&A", layout="wide")
    _ensure_session()

    deals, reps = _cached_load()
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
            f"`DATABASE_URL={'set' if os.getenv('DATABASE_URL') else 'unset'}`"
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

        threads = store.list_threads()
        options = {f"{x.title} · {x.updated_at[:19]}": x.id for x in threads}
        if not options:
            t = new_thread()
            store.upsert_thread(t)
            threads = [t]
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
            remaining = store.list_threads()
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
        "Optional LLM routing: set `OPENAI_API_KEY`."
    )

    for m in thread.messages:
        with st.chat_message(m.role):
            if m.role == "assistant" and m.result_snapshot:
                _render_assistant_message(m.content, m.result_snapshot)
            else:
                st.markdown(m.content)

    prompt = st.chat_input("Ask about pipeline, quota, Enterprise, Ironbridge…")
    if not prompt:
        return

    q = prompt.strip()
    if not q:
        return

    thread.messages.append(ChatMessage(role="user", content=q))

    last_assistant_snapshot = None
    for m in reversed(thread.messages[:-1]):
        if m.role == "assistant" and m.result_snapshot:
            last_assistant_snapshot = m.result_snapshot
            break

    if is_followup_logic_request(q) and last_assistant_snapshot:
        implementation = last_assistant_snapshot.get("implementation", "src/metrics.py (pandas)")
        thread.messages.append(
            ChatMessage(
                role="assistant",
                content=f"**Implementation:** `{implementation}`",
                result_snapshot={
                    "computation_notes": last_assistant_snapshot.get("computation_notes", ""),
                    "tables": {},
                },
            )
        )
        if thread.title == "New chat":
            thread.title = (q[:48] + "…") if len(q) > 48 else q
        _save_thread(thread)
        st.rerun()

    term = extract_definition_term(q)
    if term and last_assistant_snapshot:
        explained = explain_term_with_llm(term, last_result_snapshot=last_assistant_snapshot)
        if explained is not None:
            thread.messages.append(
                ChatMessage(role="assistant", content=format_term_explanation_md(explained), result_snapshot=None)
            )
            if thread.title == "New chat":
                thread.title = (q[:48] + "…") if len(q) > 48 else q
            _save_thread(thread)
            st.rerun()

    tail = _format_tail(thread.messages[:-1])
    result = _call_answer_question(
        q,
        deals,
        reps,
        structured_checkpoint=thread.structured_checkpoint or None,
        conversation_tail=tail or None,
    )

    snapshot = serialize_result_for_storage(result)
    body = _assistant_markdown_body(snapshot)
    thread.messages.append(
        ChatMessage(role="assistant", content=body, result_snapshot=snapshot)
    )

    thread.structured_checkpoint = build_structured_checkpoint(result)
    if not maybe_refresh_llm_summary(thread):
        append_rolling_summary(thread, result)

    if thread.title == "New chat":
        thread.title = (q[:48] + "…") if len(q) > 48 else q

    _save_thread(thread)
    st.rerun()


if __name__ == "__main__":
    main()
