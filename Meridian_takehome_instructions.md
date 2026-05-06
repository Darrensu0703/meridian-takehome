# Meridian Systems — FDE take-home (working notes)

**Last updated:** 2026-05-02  
**Related PDF:** `FDE_TakeHome_MeridianSystems.pdf` (in `E:\Quadrant\April 2026\Deloitte\`)

Duplicate of `E:\Quadrant\April 2026\Deloitte\Meridian_takehome_instructions.md` — placed here so it shows in Explorer when this folder is open. Edit one place and copy over if both should stay aligned.

This file captures planning steps and testing guidance from a Cursor conversation. **It is subject to change** — revise dates, paths, and priorities as the assignment evolves.

---

## Deliverables (from the brief)

1. **Working demo** — live or recorded; functional over polish.
2. **2–3 minute verbal framing** — what you built, what you cut, what you'd prioritize next with ~4 more hours.
3. **Brief answer** — how your prototype avoids the prior AI trust failure (hallucination), and where it still falls short.

---

## Step-by-step (practical order)

### Phase A — Data and ground truth

1. Place the **sample dataset** (separate download from Deloitte) in a project folder, e.g. `meridian_takehome/`.
2. **Inspect** files (CSV/Excel): columns, grain (deal vs rep vs day), Q1 2026 coverage, segments, stages, quota.
3. Write **3–5 golden answers** yourself (pandas/SQL in a scratch script or notebook), e.g. Enterprise vs quota, reps at risk, pipeline before end of March. The app must **match** these — not whatever an LLM invents.

### Phase B — Trust architecture

4. **Rule:** Answers come from **aggregating/filtering your tables** (or validated query results). The LLM **explains** numbers or helps with NL→query under constraints — it does **not** invent metrics.
5. **Traceability:** Each reply shows **which rows or aggregates** back the answer (snippet, IDs, SQL, or clear references) plus **caveats** when data is incomplete, ambiguous, or assumptions are needed.

### Phase C — Interface (pick one)

6. Choose **one**: CLI (`python app.py`), **notebook** (cells calling your Q&A function), or a **small web UI** (Streamlit/Gradio).
7. **Wire the flow:** natural language → structured question/query/filters → **numbers from data** → optional LLM for wording → **same numbers** in the UI with sources.

### Phase D — Deliverables prep

8. **Demo:** Run **live** or **record** (question → answer + sources + caveat; include a hard case like the **Ironbridge** question — see PDF).
9. **2–3 minute talk:** what you built, what you skipped, next 4 hours (e.g. evals, segments, auth — align with what they care about).
10. **Trust answer:** how you prevent hallucinations (ground in query results, refuse when unsupported, provenance) **and** honest limits (NL→query errors, partial data, summarization risk).

---

## Where to test your prototype

**Do not use Cursor chat** as the product under test — that is the assistant you use **while building**.

Test **inside what you built**:

| Build type | How you interact |
|------------|------------------|
| **CLI** | Terminal: run your script; type questions at the prompt. |
| **Notebook** | Run cells; `input()` or set `question = "..."` and execute the answer cell. |
| **Web app** | Browser on `localhost`; use the UI chat box. |

Use the PDF's **example queries** there and verify against your golden checks.

---

## Note on "Ironbridge"

From the brief: *"Why did we lose Ironbridge?"* is **intentionally unanswerable** from the sample data. Use it to show safe handling (no fabricated reason; clear gap + what would be needed).

---

## Chat vs CLI vs notebook (interface vocabulary)

- **Chat UI (your demo):** Small graphical chat you build (web/Streamlit/Gradio/etc.).
- **CLI:** Your script in a **terminal** — stdin/stdout loop; no special "Cursor CLI" required for the assignment.
- **Notebook:** Jupyter-style cells; good for showing tables/plots next to answers.

Cursor's own chat is **development tooling**, not the Meridian deliverable unless you explicitly scope it that way (unusual).

---

*Edit this file anytime your approach or folder layout changes.*
