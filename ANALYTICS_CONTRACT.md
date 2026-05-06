# Meridian take-home — Analytics contract (draft for review)

**Purpose:** Single source of truth for how this prototype interprets the sample CSVs. All computed answers and demos follow these rules unless a question explicitly overrides them (then we state the override in the answer).

**Data:** `data/deals.csv` (one row per deal), `data/reps.csv` (one row per rep). Join: `deals.rep_id` = `reps.rep_id`.

---

## 1. Calendar — Q1 2026 and “this quarter”

| Term | Definition |
|------|------------|
| **Q1 2026** | `close_date` is on or after **2026-01-01** and on or before **2026-03-31** (inclusive). |
| **“This quarter”** (when the user does not name another period) | Same as **Q1 2026** for this assignment dataset and timing. |

**Note:** All date filters use `close_date` unless the question asks for something else (e.g. created_date). If we ever need a different window, we say so in the answer.

---

## 2. Stages — open vs closed

| Category | `stage` values in this dataset |
|----------|--------------------------------|
| **Closed — won** | `Closed Won` |
| **Closed — lost** | `Closed Lost` |
| **Open (in pipeline)** | `Prospecting`, `Discovery`, `Proposal`, `Negotiation` |

Anything not listed above is treated as **unknown** and flagged until clarified.

---

## 3. Pipeline

| Term | Definition |
|------|------------|
| **Pipeline (default)** | All deals where `stage` is **open** (see table above). |
| **Pipeline value** | Sum of `deal_value` over those rows (subject to any **close_date** filter the question adds). |

**Closed deals** (`Closed Won`, `Closed Lost`) are **not** included in pipeline, per standard reporting usage.

**Example alignment with sample questions:** *“Total pipeline value for deals closing before end of March”* = open deals with `close_date` **≤ 2026-03-31** (and still open at time of question — i.e. not closed won/lost). We state that definition next to the number.

---

## 4. Quota attainment (Q1 2026)

| Term | Definition |
|------|------------|
| **Quota source** | `reps.quota_q1_2026` (one row per rep). |
| **Booked revenue toward Q1 quota** | Sum of `deal_value` for deals with `stage = Closed Won` and `close_date` in **Q1 2026** (Section 1). |
| **Attainment (rep level)** | Booked revenue for that rep’s deals in Q1 2026 ÷ `quota_q1_2026` for that rep (can express as %). |
| **Attainment (segment level, e.g. Enterprise)** | Sum **Closed Won** `deal_value` in Q1 2026 for deals matching the segment rule (Section 5) ÷ sum of `quota_q1_2026` for reps included in that segment rollup (see Section 5). |

We do **not** count `Closed Lost` or open pipeline toward quota attainment.

---

## 5. Segment — “Enterprise” (and other segments)

| Question type | Rule we use |
|---------------|-------------|
| Deal-level questions (e.g. pipeline, revenue by customer type) | Filter by **`deals.segment`** (`Enterprise`, `Mid-Market`, `SMB`). |
| Rep-level questions (e.g. “which reps…”) | Identify reps via **`deals`** joined to **`reps`**; segment filters apply to **`deals.segment`** unless the user explicitly asks for “reps in Enterprise *role/book*,” in which case we would use `reps.segment` and **say we switched definition**. |

**Default for phrases like “Enterprise segment”:** **`deals.segment == "Enterprise"`** unless clarified.

---

## 6. Regions and reps

| Term | Definition |
|------|------------|
| **Rep** | A row in `reps.csv`; deals attach via `rep_id`. |
| **Region** | Use `deals.region` for deal geography unless the question asks for rep home region (then `reps.region`). **Default:** deal region from `deals.csv`. |

---

## 7. Missing data, Ironbridge, and trust

| Situation | Behavior |
|-----------|----------|
| Field is blank (e.g. `loss_reason` empty on a **Closed Lost** deal) | We **do not** invent a reason. We answer from facts present; we say **loss reason is not recorded** in the data. |
| Question asks **why** we lost a deal but `loss_reason` is empty | Direct answer: **cannot be determined from this dataset**; cite the deal row(s) we used. |
| Ambiguous question | **Default:** answer using the definitions in this contract, **state those assumptions explicitly** in the response, and set **lower confidence** when the wording was vague. **Only ask** the user for clarification if the question **cannot** be answered even after applying these defaults (e.g. two incompatible interpretations with no contract rule). |

**Ironbridge:** Account **Ironbridge** appears as a closed lost deal; if `loss_reason` is empty, our prototype must **not** fabricate a cause — same rule as above.

---

## 8. Product lines and other columns

Unless the user asks about **product_line**, **manager**, or **hire_date**, we do not slice by those fields by default. If we add a slice, we show it in traceability.

---

# Part B — What goes where (system prompt vs this document)

| Artifact | Role |
|----------|------|
| **This file (`ANALYTICS_CONTRACT.md`)** | Full definitions for **you**, **reviewers**, and **debugging**. Anything ambiguous should be resolved here first, then reflected in code. |
| **System prompt (LLM)** | **Short**: instruct the model to **only explain results produced by code**, **never invent numbers or loss reasons**, and **cite uncertainty** when data is missing. Optionally paste **Section 9** below verbatim or paraphrase. The model does **not** need the whole tables above unless you want RAG; usually **code enforces** the contract. |

---

## 9. Condensed block — optional paste into LLM system prompt

Use this **after** your Python (or SQL) has already computed aggregates and optional small tables. Adjust tone but keep the constraints.

```text
You are a reporting assistant for Meridian’s sales prototype. The authoritative numbers and filters come ONLY from the application’s computed results (tables and aggregates provided in this turn). Do not invent metrics, deal outcomes, or loss reasons. If loss_reason or other fields are missing, say the dataset does not contain that detail—do not guess. Use the same definitions as the project analytics contract: Q1 2026 means close_date between 2026-01-01 and 2026-03-31; pipeline means open stages only (not Closed Won/Lost); quota attainment uses Closed Won in Q1 vs reps.quota_q1_2026; Enterprise segment defaults to deals.segment. Briefly restate the answer, then summarize traceability in plain English from the provided figures.
```

---

## 10. Change log

| Date | Change |
|------|--------|
| *(you)* | Initial draft — review Q1 bounds, segment default, pipeline date filters for interview consistency. |

---

*Revise any row after you discuss with your interviewer prep; keep code and demo aligned with the version you quote.*
