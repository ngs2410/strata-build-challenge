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

## Prerequisites

- **Python 3.12** or newer. Check with `python3.12 --version`.
- **git**, **make**.
- An **OpenAI API key**. v1 dispatches the four LLM tasks
  (version_diff, citation_verify, obligation_extraction, mapping) to
  the OpenAI responses API with structured outputs. See
  [Non-goal in PRD §6](PRD.md): tenant-chosen providers / BYO keys /
  tenant-owned fine-tuned models land post-wedge through the existing
  `ModelCaller` seam, but v1 is OpenAI only.

## Setup (from a fresh clone)

```
git clone git@github.com:ngs2410/strata-build-challenge.git strata
cd strata
python3.12 -m venv env3.12
env3.12/bin/pip install --upgrade pip
env3.12/bin/pip install -e '.[dev]'
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...
```

The `env3.12` directory name is what the Makefile targets expect. If
you prefer a different venv path, override the Python interpreter
when running the commands below (`PYTHON=/path/to/python make eval`
isn't wired up — easier to just name your venv `env3.12`).

## Run the tests

```
env3.12/bin/pytest tests/
```

The suite is network-free (an autouse fixture rewrites every LLM task
to a local `stub://` backend) and runs in about 1.5 seconds. Expect
122 tests to pass.

Also useful:

- `make test-cov` — run with coverage; writes a terminal report.
- `make lint` — `ruff check src scripts tests`.
- `make format` — `ruff format` + `ruff check --fix`.

## Run the pipeline end-to-end

```
env3.12/bin/python -m scripts.main_detect_changes \
  --tenant_id=verdant_grid --environment=prod
```

This runs the full 7-stage pipeline against the FERC Order 2222 NOPR
→ Final Rule excerpts shipped in `data/tenants/verdant_grid/` and
Verdant Grid's seeded company context (8 obligations / 3 projects /
5 documents). Outputs land under `data/tenants/verdant_grid/prod/`:

- `proceedings.json` — ingested ProceedingVersions.
- `obligations.json` / `projects.json` / `documents.json` — company
  context.
- `changes.json` — detected material changes with grounded + verified
  citations.
- `extracted_obligations.json` — obligations extracted per version.
- `mappings.json` — change → affected items mappings.
- `review_items.json` — Rachel's prioritized queue.
- `audit_log.jsonl` — append-only audit of every state transition.

Expect ~20 LLM calls per cold-cache run; ~30s wall-clock.
`diskcache` under `.model_cache/` makes subsequent runs <1s.

Script flags (all optional):

- `--tenant_id=<id>` (default `verdant_grid`): tenant to run.
- `--environment=<env>` (default `prod`): environment within tenant.
- `--dry_run`: compute everything but don't persist.
- `--k=<N>`: smoke-test on first N items (no-op at v1 cardinality).
- `--profile`: dump a cProfile top-50 to stderr.
- `--confirm_llm`: pause before every LLM call, show the rendered
  prompt and token-count estimate, and prompt for y/n. Useful for
  prompt debugging.

## Open the review UI

```
env3.12/bin/python -m scripts.main_review
```

Then open `http://127.0.0.1:8000/` in a browser. You'll be
redirected to the Verdant Grid queue. The UI has two pages:

- `/queue?tenant_id=verdant_grid&environment=prod` — Rachel's
  prioritized review queue. Each row shows the detected change, the
  cited passage text with the citing span `<mark>`-highlighted
  inline, the verification status, the mapping's affected items,
  recommended action, routing destination, and three action buttons
  (**approve** / **override** / **escalate**) that write an
  AuditEvent and transition the ReviewItem.
- `/audit?tenant_id=verdant_grid&environment=prod` — read-only
  chronological AuditEvent list. Rollback is deferred to future
  work (TDD §8.8).

Flags: `--host`, `--port` (default 8000), `--reload` (dev-time
auto-reload).

## Run the evals

```
env3.12/bin/python -m scripts.main_eval
```

Runs the full pipeline against `data/evals/gold.json` (one case,
16 hand-labelled items — 3 changes, 3 citation verifications, 7
obligation extractions, 3 mappings), scores precision / recall / F1
per stage, prints a text report with a reliability diagram, and
writes a timestamped JSON report to `output/evals/<timestamp>.json`.

For a committed, dated submission artifact:

```
make eval-report
```

This writes `docs/eval-results/eval-results-<YYYY-MM-DD>.md` — a
headline-numbers markdown table plus the full text report, suitable
for committing alongside a submission so the trust-metric numbers
are reproducible from the repo without re-running the eval.

Script flags: all the main_detect_changes flags above plus
`--gold_file`, `--output_dir`, `--md_output`.

## Project layout

```
src/                          Library code
  config.py                   Pydantic models + load_domain_config
  ingest.py                   Load proceedings + company context
  version_diff.py             LLM task: detect material changes
  citation/
    citation_grounding.py     Resolve passage_id + offsets
    citation_verify.py        LLM task: verify citations (depth area)
  obligation_extraction.py    LLM task: extract obligations
  mapping.py                  LLM task: map changes to internal state
  review_queue.py             ReviewItem construction + apply_action
  audit_log.py                Append-only JSONL audit log
  web_app.py                  FastAPI app (/queue + /audit)
  model_caller.py             ModelCaller facade (openai / jev / stub)
  prompt_calibration.py       Shared rubric constants + taxonomies
  evals.py                    Eval harness (depth area)
  stores.py                   JsonStore[T] + JsonlStore[T] facades
  backends/                   openai_backend.py, jev_backend.py, stub_backend.py
  prompt_registry.py          Task name → prompt + response + stub
  tenant_registry.py          Tenant list + DEFAULT_ENVIRONMENT
  telemetry.py                structlog JSON to stderr
  llm.py                      PromptSpec + CallSpec dataclasses
config/
  default_domain_config.json  Baseline DomainConfig
data/
  tenants/verdant_grid/       FERC Order 2222 excerpts + company context
  evals/gold.json             Hand-labelled gold set
templates/                    Jinja2 templates (queue.html, audit.html)
scripts/                      CLI entry points (main_*.py)
tests/                        Pytest tests (network-free; stub backend)
```

## Known limitations

- **One tenant, one proceeding, one subsection** — PRD §6 scope.
  Multi-tenant / multi-jurisdiction / full-document ingest are
  deferred.
- **Single routing destination per change** — PRD §7 and TDD §8.6
  cover the v2 shift to primary + CC routing.
- **No rollback** — `/audit` is read-only; TDD §8.8 covers the
  deferred fold-over-log + replay-to-timestamp design.
- **No auth** — TDD §4.7 covers the production OIDC/SSO shape.
- **OpenAI only** — v1 ships the `openai://` dispatch seam and a
  `jev://` NotImplementedError shell; alternative providers land
  through the existing `Backend.matches` facade.
