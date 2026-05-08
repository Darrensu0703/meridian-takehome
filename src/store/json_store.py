"""File-backed conversation store (no DB required)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ..conversation_models import ChatThread

_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "chat_store" / "threads.json"


class JsonConversationStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load_raw(self) -> dict:
        if not self._path.exists():
            return {"threads": {}}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"threads": {}}

    def _save_raw(self, data: dict) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    def list_threads(self) -> list[ChatThread]:
        with self._lock:
            raw = self._load_raw()
        threads = [ChatThread.from_dict(t) for t in raw.get("threads", {}).values()]
        threads.sort(key=lambda t: t.updated_at, reverse=True)
        return threads

    def list_thread_summaries(self):
        from . import ThreadSummary

        with self._lock:
            raw = self._load_raw()
        items = []
        for d in raw.get("threads", {}).values():
            items.append(
                ThreadSummary(
                    id=d.get("id", ""),
                    title=d.get("title", ""),
                    created_at=d.get("created_at", ""),
                    updated_at=d.get("updated_at", ""),
                )
            )
        items.sort(key=lambda s: s.updated_at, reverse=True)
        return items

    def get_thread(self, thread_id: str) -> ChatThread | None:
        with self._lock:
            raw = self._load_raw()
        d = raw.get("threads", {}).get(thread_id)
        return ChatThread.from_dict(d) if d else None

    def upsert_thread(self, thread: ChatThread) -> None:
        with self._lock:
            raw = self._load_raw()
            if "threads" not in raw:
                raw["threads"] = {}
            raw["threads"][thread.id] = thread.to_dict()
            self._save_raw(raw)

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            raw = self._load_raw()
            raw.get("threads", {}).pop(thread_id, None)
            self._save_raw(raw)
