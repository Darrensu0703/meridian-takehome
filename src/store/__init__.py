"""Conversation persistence: JSON files by default, PostgreSQL when DATABASE_URL is set."""

from __future__ import annotations

from typing import Protocol

from ..conversation_models import ChatThread
from .factory import get_conversation_store


class ConversationStore(Protocol):
    def list_threads(self) -> list[ChatThread]: ...

    def get_thread(self, thread_id: str) -> ChatThread | None: ...

    def upsert_thread(self, thread: ChatThread) -> None: ...

    def delete_thread(self, thread_id: str) -> None: ...


__all__ = ["ConversationStore", "get_conversation_store"]
