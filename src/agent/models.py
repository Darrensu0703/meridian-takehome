from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


AgentStepType = Literal["commentary", "tool_call", "tool_result", "final"]


@dataclass(frozen=True)
class AgentStep:
    """Visible agent trace step. This is not hidden reasoning."""

    step_type: AgentStepType
    content: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_type": self.step_type,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_output": self.tool_output,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "AgentStep":
        step_type = data.get("step_type")
        if step_type not in {"commentary", "tool_call", "tool_result", "final"}:
            step_type = "commentary"
        return AgentStep(
            step_type=step_type,
            content=data.get("content"),
            tool_name=data.get("tool_name"),
            tool_input=data.get("tool_input"),
            tool_output=data.get("tool_output"),
        )


@dataclass(frozen=True)
class AgentRunResult:
    final_content: str
    steps: list[AgentStep]
    result_snapshot: dict[str, Any] | None = None

