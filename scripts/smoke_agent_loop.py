r"""End-to-end agent smoke test that goes through the live OpenAI model.

Asks the agent two questions that previously fell off a cliff with the
hand-rolled JSON loop, and prints the resulting trace. Requires
``OPENAI_API_KEY`` and the Postgres ontology to be reachable.

Usage (from repo root):

    .\.venv\Scripts\python.exe scripts\smoke_agent_loop.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


from src.agent import run_agent  # noqa: E402


def _print_run(label: str, question: str) -> None:
    print(f"\n=== {label}: {question!r} ===")
    result = run_agent(question)
    for i, step in enumerate(result.steps, start=1):
        meta = ""
        if step.tool_name:
            meta = f" tool={step.tool_name}"
        body = step.content or step.tool_input or step.tool_output or ""
        body_str = str(body)
        if len(body_str) > 240:
            body_str = body_str[:240] + "…"
        print(f"  [{i}] {step.step_type}{meta}: {body_str}")
    print(f"  FINAL: {result.final_content[:400]}")


def main() -> int:
    _print_run("managers list", "give me the list of managers")
    _print_run("schema", "what columns does the deal ontology expose?")
    _print_run(
        "pipeline before April 1",
        "How much pipeline value do we have tied to deals expected to close before April 1?",
    )
    _print_run(
        "deals by region",
        "How many deals do we have per region? sort by count descending.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
