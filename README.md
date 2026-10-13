# Strata

A regulatory change-to-action workspace for in-house regulatory affairs
teams at regulated enterprises. Strata ingests versioned regulatory
proceedings alongside the company's own obligations, projects, and
documents; detects material changes between versions; cites the exact
source passage behind every claim; maps each change to the specific
internal state it affects; recommends an action; and routes it to the
right internal reviewer, with a full audit trail.

Submission to the AI Fund Engineer-in-Residence Build Challenge.

**First user.** Rachel Chen, Senior Manager of Regulatory Affairs at
Verdant Grid, a mid-size US investor-owned electric utility. v1 maps
changes between the draft (NOPR) and final text of FERC Order 2222 —
the 2020 rule on distributed-energy-resource aggregation — into
Verdant's internal obligations, projects, and documents.

## Governing documents

Read these first; the panel review follows roughly this order.

- **CHALLENGE.md** — the build-challenge brief this repo responds to.
- **PRD.md** — product requirements. First user, why alerts and search
  are insufficient, the wedge workflow, trust and adoption metrics,
  expansion beyond utilities, non-goals, open questions.
- **TDD.md** — technical design. Architecture, data model, the 7-stage
  pipeline (ingest → diff → cite → extract → map → route →
  review/audit), cross-cutting services, evals, scaling pathway, open
  technical questions.

Supporting references (consulted while writing code):

- **CLAUDE.md** — code style, mandatory design-compliance rules,
  tooling, engineering principles.
- **PROMPT_CONVENTIONS.md** — authoring conventions for LLM-task
  prompts (PromptSpec sections, response-model rules, integer-index
  selection).
- **TEST_QUALITY.md** — rules for writing tests that are actually
  useful.
- **JEV.md** — reference for Jev, TypeSafe AI's System One
  discrimination model (named across the TDD as the production path
  for structured discrimination tasks like citation verification,
  change-type classification, and routing).
- **PREP_MATERIAL.md** — AI Fund's reviewer guide; informs how the
  submission is structured.

## Status

Design phase. Prototype implementation is next.

- [x] PRD complete
- [x] TDD complete
- [ ] End-to-end pipeline (`src/ingest.py` → `src/version_diff.py` →
      `src/citation/` → `src/obligation_extraction.py` →
      `src/mapping.py` → `src/review_queue.py` → `src/audit_log.py`)
- [ ] Hand-labelled gold set (`data/evals/gold.json`) and eval harness
      (`scripts/main_eval.py`)
- [ ] Minimal FastAPI web view (`/queue` + `/audit` routes)

## Running the prototype

Instructions will land here once the implementation does — the exact
command to run the core workflow end-to-end, the exact command to run
the tests, access notes, and any known limitations, matching the
submission-template labels in PREP_MATERIAL.md.
