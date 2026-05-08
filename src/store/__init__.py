"""Conversation persistence: JSON files by default, PostgreSQL when DATABASE_URL is set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..conversation_models import ChatThread
from .factory import get_conversation_store


@dataclass(frozen=True)
class ThreadSummary:
    """Lightweight thread descriptor for list views (no messages)."""

    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationStore(Protocol):
    def list_threads(self) -> list[ChatThread]: ...

    def list_thread_summaries(self) -> list[ThreadSummary]: ...

    def get_thread(self, thread_id: str) -> ChatThread | None: ...

    def upsert_thread(self, thread: ChatThread) -> None: ...

    def delete_thread(self, thread_id: str) -> None: ...


__all__ = ["ConversationStore", "ThreadSummary", "get_conversation_store"]
