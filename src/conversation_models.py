"""
Thread + message models and structured BI checkpoint (JSON-serializable).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Role = Literal["user", "assistant", "system"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ChatMessage:
    role: Role
    content: str
    created_at: str = field(default_factory=utc_now_iso)
    """Assistant messages may carry a compact snapshot for reload / provenance."""
    result_snapshot: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "result_snapshot": self.result_snapshot,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ChatMessage:
        return ChatMessage(
            role=d["role"],
            content=d["content"],
            created_at=d.get("created_at", utc_now_iso()),
            result_snapshot=d.get("result_snapshot"),
        )


@dataclass
class ChatThread:
    id: str
    title: str
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    messages: list[ChatMessage] = field(default_factory=list)
    """Rolling text summary for long threads (checkpoint memory)."""
    summary_checkpoint: str = ""
    """Structured BI state: metric, dimensions, filters — deterministic layer."""
    structured_checkpoint: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": [m.to_dict() for m in self.messages],
            "summary_checkpoint": self.summary_checkpoint,
            "structured_checkpoint": self.structured_checkpoint,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ChatThread:
        return ChatThread(
            id=d["id"],
            title=d.get("title", "Chat"),
            created_at=d.get("created_at", utc_now_iso()),
            updated_at=d.get("updated_at", utc_now_iso()),
            messages=[ChatMessage.from_dict(x) for x in d.get("messages", [])],
            summary_checkpoint=d.get("summary_checkpoint", ""),
            structured_checkpoint=dict(d.get("structured_checkpoint") or {}),
        )


def new_thread(title: str = "New chat") -> ChatThread:
    return ChatThread(id=str(uuid.uuid4()), title=title)
