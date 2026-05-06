"""
Run from the `meridian-takehome` folder (so `src` is importable):

  py -3 run_demo.py "How is Enterprise tracking against quota?"

Ways to ask a question (pick one):

  1) Terminal: pass the question as arguments (see above).
  2) Edit this file: set DEMO_QUESTION below, then run `py -3 run_demo.py` with no args.
  3) Interactive chat in the terminal: `py -3 run_demo.py -i` — type a question, Enter;
     repeat; blank line to quit.
  4) Notebook: open `meridian_demo.ipynb` in this folder (like a notebook workflow).
"""
from __future__ import annotations

import sys

from src.answer import answer_question, format_answer_for_console
from src.metrics import load_data

# --- (2) Type your question here when you do not pass CLI args -----------------
# Leave "" to use the default question at the bottom of main().
DEMO_QUESTION = ""


def _parse_argv(argv: list[str]) -> tuple[list[str], bool]:
    """Return (non-flag tokens, interactive?)."""
    interactive = False
    parts: list[str] = []
    for a in argv:
        if a in ("-i", "--interactive"):
            interactive = True
        else:
            parts.append(a)
    return parts, interactive


def _run_one(q: str, deals, reps) -> None:
    out = answer_question(q, deals, reps)
    print(format_answer_for_console(out))
    print()


def main() -> None:
    tokens, interactive = _parse_argv(sys.argv[1:])

    if interactive:
        deals, reps = load_data()
        print("Interactive mode — type a question, Enter. Empty line to quit.\n")
        while True:
            try:
                q = input("Question> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not q:
                break
            _run_one(q, deals, reps)
        return

    cli_q = " ".join(tokens).strip()
    if cli_q:
        q = cli_q
    elif DEMO_QUESTION.strip():
        q = DEMO_QUESTION.strip()
    else:
        q = "pipeline closing before March"

    deals, reps = load_data()
    _run_one(q, deals, reps)


if __name__ == "__main__":
    main()
