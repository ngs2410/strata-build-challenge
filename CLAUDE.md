## Documentation

- The challenge brief is in CHALLENGE.md and the AI Fund prep material is in PREP_MATERIAL.md — these frame the user, scope, and evaluation criteria
- The product requirements are in PRD.md (first regulatory-affairs user, why alerts and search are insufficient, the first change-to-action wedge workflow, trust and adoption metrics, expansion beyond utilities)
- The technical design is in TDD.md (ingestion, version diffing, evidence-linked extraction, company-context data model, citation verification, confidence and escalation logic, reviewer routing, audit history and rollback, evals, data isolation, security)
- Quality rules for testing are in TEST_QUALITY.md
- Contradictions between these documents should be posed as questions for the human to resolve
- LLM prompt authoring conventions (PromptSpec, CallSpec, json_example placeholders, list-index selection, etc.) are in PROMPT_CONVENTIONS.md — read this before writing or substantially editing any prompt
- The code style, tooling, and engineering principles below are important

## Codebase Structure

V1 layout, calibrated for build-challenge scope (one proceeding, one subsection, ~16-item company context). Flat where possible; sub-packages reserved for depth-area modules. Will evolve as the build proceeds; keep this section in sync with the actual tree.

Two depth areas are called out explicitly in the tree: **citation grounding** (the two-step pattern of grounding + post-generation verification) and **evals** (precision/recall against a hand-labeled gold set). The remaining modules support the end-to-end loop without claiming depth.

```
src/                                Python library code
  config.py                         Pydantic models (DomainConfig, TaskModelConfig, Passage, Obligation, Project, Document, ProceedingVersion, Change, Mapping, ReviewItem, AuditEvent) + load_domain_config(tenant_id, environment)
  llm.py                            PromptSpec, CallSpec, render helpers
  model_caller.py                   ModelCaller — LLM dispatch, diskcache, retries
  prompt_registry.py                Task name → prompt builder function
  tenant_registry.py                Tenant list + DEFAULT_ENVIRONMENT constant
  stores.py                         Store[T] ABC + JsonStore[T] + JsonlStore[T] concretes; typed singletons for proceeding/obligation/project/document/change/extracted_obligation/mapping/review_item/asset_decision/audit
  ingest.py                         Load proceeding versions + company context; passage model; normalize helpers
  version_diff.py                   Material-change detection + change summary (one LLM task; draft_vs_final is a Literal on the version model, not a classifier)
  citation/                         DEPTH AREA — kept as a sub-package
    citation_grounding.py           Pin every claim to its source passage (passage_id + offsets, back-mapped from a verbatim citing_span the LLM emits)
    citation_verify.py              Post-generation verifier — confirms the cited passage supports the claim
  obligation_extraction.py          Extract obligations from passages (LLM task)
  mapping.py                        Change → affected items with per-item (proposed_action, proposed_route) proposals; one LLM task over the capped candidate set (per-item routing closed TDD §8.6 in v1)
  review_queue.py                   Rachel's queue + per-pair apply_asset_decisions (approve/override/dismiss/escalate per change×asset pair, single-row or bulk); ReviewItem.status folds from AssetDecisions via _fold_status
  audit_log.py                      Append-only JSONL log + log_event helper (fold-to-current-state + rollback-to-timestamp deferred per TDD §8.8)
  web_app.py                        FastAPI app — /queue + /audit GETs, /queue/{id}/decide POST, with Jinja2 templates
  evals.py                          DEPTH AREA — eval harnesses for citation precision, mapping precision, change-detection quality
config/
  default_domain_config.json        Baseline config (copied for new tenants)
data/
  tenants/verdant_grid/
    proceedings/                    Text excerpts of Order 2222 NOPR + Final Rule
    company_context.json            Seeded obligations, projects, documents for Verdant Grid
    prod/                           Committed demo snapshot of pipeline outputs (see README "Demo snapshot")
  evals/
    gold.json                       Hand-labeled gold set for precision/recall metrics
templates/
  queue.html                        Rachel's review-queue page (per-pair table + bulk toolbar; Jinja2)
  audit.html                        Per-tenant+env audit-log page (Jinja2)
scripts/                            CLI entry points (all main_*.py, use absl flags)
  main_detect_changes.py            Run end-to-end pipeline (ingest + diff + verify + extract + map + enqueue) for a tenant/env; --stub_all for air-gapped regen
  main_review.py                    Launch the FastAPI web view (uvicorn wrapper)
  main_eval.py                      Run evals end-to-end
tests/                              Pytest tests — test file naming: test_version_diff.py (not test_diff_version_diff.py)
```

## Code Style

- 2-space indentation
- Single quotes
- ensure_ascii=false in json.dumps
- absl flags for scripts
- no docstrings on functions or classes. **Module-level docstrings are the exception** — every file in `src/` and `scripts/` may start with a triple-quoted module docstring that explains, at staff-engineer level, what the module does, how it fits into the pipeline, and any non-obvious implementation details (invariants it enforces, caching behavior, failure modes, upstream/downstream contracts). Function and class behavior still goes in identifier names, not prose.
- no 'magic' literals, create constants instead. For v1 we ship a single storage backend (JSON); when a second lands, backend selectors live in `src/stores.py` as `BACKEND_*` constants. Facade modules export a public `FACADE_NAME` constant at module top that other modules import (don't re-declare or hardcode).
- don't add silent fallbacks — this includes `.get('foo', [])`, `x or ''`, `x or []`, `x | default`, `if x is None: x = default`. Use direct access `['key']` and let KeyError/TypeError propagate.
- if a .get() default is intentional (boundary input, sparse config, optional field), mark the line with `# intentional default — <who writes this data and why the key may be absent>`. The comment MUST name the producer. If the producer is our own code, the default is WRONG — use direct access. Only boundary inputs from outside the system (user input, platform metadata, external APIs) qualify.
- fail fast and allow exceptions to happen — don't add fallback paths, degraded modes, or warning-and-continue when a required resource is missing. If the system needs a file, read it directly and let FileNotFoundError propagate. A crash with a clear traceback is more useful than silently using wrong data. This includes: NEVER add a default value to a Pydantic model field (e.g. `= None`, `= []`, `= 0`) to make old serialized data load — if deserialization fails because data is stale, the answer is ALWAYS to rebuild the data by re-running the pipeline. Adding a default is a silent fallback.
- when tempted to add a fallback, treat it as a signal: why might this data be missing? (1) backward compatibility with old data — not a concern, we rebuild the world. (2) upstream producer might omit the key — trace the producer and verify the contract; if genuinely optional, ask the user. (3) test fixtures don't include the field — fix the fixtures, don't weaken production code.
- never wrap a call in `try/except` at the call site to hedge 'maybe-not-initialized' or 'maybe-failed' cases — that's a silent fallback in disguise that hides whether the underlying state is actually OK or whether you're papering over a real bug. If 'nothing to do' is a valid no-op for the callee (e.g. shutdown when nothing was started, close when nothing was opened), make the callee return early gracefully and document the no-op there. If 'not initialized' is a real bug, let the AttributeError / NameError / RuntimeError propagate at the call site and fix the initialization order. Either way, the call site stays a single clean line. Bad: `try: shutdown_telemetry(); except RuntimeError: pass  # never initialized`. Good: `shutdown_telemetry()` (and the function handles its own no-op case).

## Strata product invariants

These are non-negotiable properties of the Strata data model, not features that can be bolted on later. Design and code reviews should enforce them.

- **Every extracted claim carries a verifiable citation back to its source passage.** Claims without a passage reference are a bug. Citations are structural (passage id + offsets, not a free-text 'see §4.2' string). Version edits that re-anchor passages are handled inline in `version_diff.py` at v1 scale; a dedicated alignment module is a post-wedge expansion.
- **Draft vs final is a first-class attribute on every proceeding version**, not a free-text tag. Downstream mapping, scoring, and reviewer routing all branch on it; make it a `Literal['draft', 'final']` on the version model.
- **Low-confidence interpretations escalate to a human reviewer — the system fails to a reviewer, not to a guess.** The confidence threshold is a `DomainConfig` hyperparameter. Below threshold, the extraction is persisted with `status='escalated'` and routed; it is NEVER silently dropped or coerced.
- **Project state is a fold over an append-only audit log, not a mutable column.** `current_status`, `current_owner`, `current_action` are read-time projections. All state transitions go through the audit log so rollback is a replay-to-timestamp operation, not an UPDATE.
- **Data isolation is per `(tenant_id, environment)`.** Every facade method takes `(tenant_id, environment, ...)` together. See the Engineering-principles tenant rule below.

## Tooling

- use pytest. Coverage is opt-in via `make test-cov` (CI / explicit). Default `pytest tests/` skips the `integration` and `ml` markers; opt them in with `-m integration` / `-m ml`.
- use ruff for formatting and linting
- use structlog for structured logging — JSON logs to stderr in v1. OpenTelemetry Collector + Grafana stack (Tempo / Loki / Prometheus / Grafana) is a post-wedge addition if observability needs grow.
- use diskcache for LLM outputs and any expensive inference
- use FastAPI + Jinja2 server-rendered templates for Rachel's review-queue web view — no JS framework in v1
- use openai responses API for LLM calls, and openai services for evaluation and fine-tuning models
- use the current frontier OpenAI model as the default for all LLM calls (runner, critic, extraction, mapping) — pin the exact model id in `default_domain_config.json`, not in code
- use dotenv to load the environment
- v1 persistence is JSON + JSONL on disk (via `src/stores.py`'s `JsonStore[T]`). SQL as system of record is the production shape; add a Postgres backend when a workflow needs it, not before. The `Store[T]` ABC exists so adding one is a drop-in, not a refactor.
- radon (complexity) and mutmut (mutation testing) are available but not prescribed in v1 — run manually if you want the signal

## Engineering principles

- simplicity is priority #1, keep things simple — simplicity means elegant over hacky — no code smells — no repetition — Unix principles — readable — predictable — conventional. Simplicity doesn't necessarily mean the fewest lines of code, it means the least likely to lead to surprise outcomes and bugs. If you find yourself doing something that an experienced, senior engineer wouldn't do — stop and ask for guidance rather than pushing forward with a contorted solution.
- prefer correctness over workarounds. When you find a bug, broken assumption, or missing piece, the default is to fix it properly — not to route around it, paper over it with a default, or quarantine the failing case. Workarounds are only acceptable when correctness is genuinely expensive (rough threshold: >~5 follow-on tasks of work). Below that bar, do the right thing now; the goal is to reach correctness as soon as possible. If you're unsure whether the fix crosses the threshold, surface the choice and ask — don't silently pick the workaround.
- speed is priority #2, keep things fast, vectorize where possible, latency regressions are errors
- coverage is priority #3, cover >90% of pure functions, prompts, constants, models etc.
- beware of caching that introduces fragile state
- use orjson, vectorization, numpy and other performant tooling by default
- write I/O and pure functions separately from the start to facilitate testing
- write tests for all pure functions, test quality rules are described in TEST_QUALITY.md
- BEFORE writing or modifying any test, re-read TEST_QUALITY.md — this is mandatory, not advisory. When a test fails because a mock is inadequate, fix the mock; never weaken assertions to work around mock limitations
- evaluation first — ML/LLM components must have an evaluation harness and eval data before they ship. Reviewers will look for at least one concrete failure path plus tests or evaluations.
- determinism — seeds, versions, snapshots where possible
- prefer to use explicit schemas unless they break simplicity
- no defaults or config in code, be dynamically reconfigurable
- DRY, don't repeat yourself, check for existing functionality before adding a new function
- YAGNI, make the smallest, tightest change to solve the problem
- tenants represent natural separation of functionality, infrastructure, and data isolation — a regulated enterprise is a tenant
- environments are a peer of tenants, not a default. Every tenant-keyed facade method takes `(tenant_id, environment, ...)` together. Service-layer code (anything in `src/` that calls those facades) MUST accept `environment: str` as a sibling parameter to `tenant_id`, plumbed from a CLI flag (`--environment`) or HTTP request parameter. Hardcoding `DEFAULT_ENVIRONMENT` as a literal at a facade call site is a bug — the runtime env is being silently dropped. `DEFAULT_ENVIRONMENT` is legitimate ONLY in: (a) parameter-signature defaults on facade ABCs/concretes, (b) CLI flag defaults, (c) test fixtures, (d) the constant's own definition site in `src/tenant_registry.py`.
- v1 persistence is JSON + JSONL on disk; the audit log is a JSONL file loaded and folded in memory. SQL as system of record is the production shape — add Postgres via a new `Store[T]` concrete when a workflow needs it, not before. The Pydantic schema is the contract in both cases.
- design for replacement, use facades around custom/third-party interfaces
- when a second backend lands (Postgres, remote vector store, external API), dispatch by URL-scheme prefix or string-prefix goes through a facade ABC (`Backend.matches(identifier) -> bool` + `Backend.call(...)`) — never an inline `if x.startswith('foo:'): ... elif x.startswith('bar://'): ...` ladder in the caller. In v1 the rule is forward-looking (we ship one backend); the facade shape reserves the slot.
- choose boring, proven technology
- no agent frameworks
- use structured logs and log everything
- explicit unit economics (per token, per run, per tenant, per proceeding)
- API first — HTTP JSON via FastAPI is the v1 programmable surface. gRPC and MCP are post-wedge if a programmatic consumer demands them. The point stands: a programmable surface is a product requirement, not an afterthought.
- prefer strings over enum-like data structures
- often things are not just 'boolean' (life is more complex) so doubt the use of boolean types — in Strata specifically, 'draft' / 'final' is a `Literal`, not a boolean `is_final`
- prompts are versioned, reviewed, and rollbackable — treat them as release artifacts
- every model call has a purpose, acceptance criteria, and known failure modes
- diskcache + explicit logging keep LLM spend visible during the build. Hard token/compute ceilings (per-run, per-tenant) are a post-wedge addition — add them when a workflow could plausibly blow past the OpenAI free-tier burn rate.
- systems degrade gracefully and fail closed — partial results are better than nothing, but low-confidence results escalate rather than silently ship
- no irreversible vendor dependencies
- replayability is mandatory — tooling to replay logged API calls
- overload handling (rate limits, queues, circuit breakers, deterministic load shedding) is a post-wedge concern — v1 scale (one tenant, ~7 LLM calls per end-to-end run) doesn't warrant it. Add it when real traffic materializes.
- scripts should always have a `--profile` flag that will use cProfile and log the top 50 most time consuming functions
- scripts should have a `--dry_run` flag that shows what they would do rather than actually doing it
- scripts should have a `--k` flag that allows the script to run over a small number of examples as a smoke test
- actively review the code you write — ensure that it is simple, modular, readable, composable, testable, reusable
- find the simplest, clearest and most straightforward way to solve a problem but recognize that sometimes a rewrite in light of new requirements is the simplest approach
- code is not sacred, it's ok to rewrite to simplify as long as we don't lose required functionality
- solve the simplest problem first and expand from there — sometimes getting 'hello world' working is enough
- don't use Postgres JSONB columns — they kill performance and violate the rule about requiring schemas. This rule is about the SHAPE of the data model, not the column type: the fix is to break the object's properties out into proper columns (and child tables / join tables for nested structures and lists), not to swap `JSONB` for `TEXT` and stuff a serialized JSON blob into it. A JSON string in a TEXT column is the same anti-pattern — unindexable, unqueryable, no schema enforcement — and is equally forbidden. If a field feels like it 'wants' to be JSONB, that's the signal to design a relational schema for it.
- there should be one canonical way to do things. Before implementing anything material, ensure that it doesn't already exist somewhere else in the code.
- before adding any new external service call, grep for existing facade modules in src/ that already wrap that service — never create a parallel integration
- never use subprocess to call Python code in the same codebase — if a function is importable, call it directly. Generating Python source code as strings is totally prohibited.
- when multiple CLI flags always co-occur with the same values, centralize them in a lookup table keyed by a single identifier (e.g. PROMPT_REGISTRY) — don't make the user specify N things determined by 1
- encapsulation is good — avoid entanglements between modules by having clear roles and responsibilities, and interfaces. OO design for horizontal facades is totally fine.
- services own their I/O — a service determines where it writes output and returns paths to the caller. Callers should not pass output directories or file paths into services.
- each LLM task gets its own module with the prompt builder, response model, and CallSpec. Don't put LLM task definitions in orchestrator modules.
- when data is serialized by one path and deserialized by another, use a single shared model and load/save function pair — never manually construct or parse the same JSON schema in multiple locations. If two code paths need the same data, they must go through the same function.
- when the same group of parameters always travel together through function calls, bundle them into a model and pass the model — don't thread N individual kwargs through M layers of calls
- in an AI/ML oriented project like this we should always be thinking in terms of hyperparameters and parameter tuning. Where there are opportunities to parameterize decisions in LLM/extraction/scoring pipelines we should be taking those opportunities and capturing the default hyperparameter in config.
- when asking an LLM to make assessments of text, make sure it has sufficient context for the text
- when providing text context to an LLM make sure it is broken cleanly on word boundaries
- backward compatibility is NOT a concern in this build challenge — we rebuild the world on each run. When a new field is added to a model, old data that lacks it MUST fail to load. The fix is to re-ingest, never to add defaults. This is non-negotiable.
- web app performance comes from query design, not language choice. Fix N+1 patterns with query consolidation, Postgres-side aggregation, and materialized views before reaching for async or language swaps. Use ThreadPoolExecutor for parallel independent queries; async only for genuinely I/O-concurrency-bound services.
- anticipate when circular imports are likely to happen and move commonly needed code and constants with no dependencies to `*_utils.py` or `*_constants.py` modules to avoid circular imports
- destructive 'reset/wipe' operations against a shared directory (tenant dirs, data dirs, anything other services also write into) must enumerate the specific artifacts this service owns and `unlink(missing_ok=True)` each one — never `shutil.rmtree(dir)`. Maintain the owned-file list as a module-level constant so 'what does this service own?' is grep-able and adding a new output file is obvious. If the enumeration would be 'everything in this directory,' the directory is shared — refactor to own a subdirectory instead.

## MANDATORY design compliance rules

Universal rules that catch recurring mistakes at plan-authoring time. Each rule has a `Rule:` (what to check), a `Why:` (the failure it prevents), and a `How to check:` (a mechanical audit step). Every Plan-agent prompt MUST include this section verbatim.

- **Rule — Tests must NOT import from `scripts/main_*.py`.** Module-level `absl` flags pollute the global `FLAGS` namespace and break any test that resets and re-parses. Refactor scripts as `src/X_lib.py` (library) + thin `scripts/main_X.py` (flags + `_run` calling into the lib). Tests import from the lib.
  - **Why:** every session eventually adds a new script + a matching test; if the test imports the script, an unrelated test that also parses FLAGS starts failing.
  - **How to check:** grep the plan for `tests/test_*.py` file changes. If a new test's imports include `from scripts.main_` or `import scripts.main_`, the design must split into `X_lib.py` first.

- **Rule — Cache keys are plaintext JSON dicts, NOT opaque SHA-256 hashes.** Hashed keys can't be enumerated / renamed / copied without re-running upstream. Encode identity components in a JSON dict; hash that dict only if the storage layer requires a fixed-size key (and keep the plaintext key alongside).
  - **Why:** a hashed cache becomes dead weight on any key-shape change — the entries can't be renamed in place, so expensive LLM calls get repaid for a cosmetic rename.
  - **How to check:** grep the plan for `cache_key`, `hash`, `sha256`, `hashlib`. Every cache-key construction must be greppable / renameable / copyable.

- **Rule — Respect encapsulation. Reuse existing modules; do NOT re-generate state a module already owns.** When state or a computation lives on a dedicated module or class (`src/config.py`'s `load_domain_config(tenant_id, environment)` owns config loading + tenant overrides; `DomainConfig` is the resulting Pydantic model; `src/stores.py`'s `JsonStore[T]` facades own their own I/O; LLM dispatch lives on `ModelCaller`), route through that module. Do not open the config file directly with `orjson.loads(open(...))` when `load_domain_config(...)` is one import away; do not reconstruct a `DomainConfig` by dict-merging JSON blobs when the loader already returns one; do not reach into `_private` attributes from outside the owning module. If the module is missing a function or method you need, ADD it there — do not inline the equivalent in the caller.
  - **Why:** re-reading state introduces two sources of truth that drift. Reaching around a facade also breaks the backend contract — the facade exists so callers stay backend-agnostic when a second concrete lands.
  - **How to check:** grep the plan for `orjson.loads(`, `json.loads(`, `open(`, `Path(...).read_bytes()`, `Path(...).read_text()`, `DomainConfig(**`, `TaskModelConfig(**`, direct file-path constructions matching config / state artifacts. For each hit, ask 'is there a module whose job this already is?' If yes, route through that module. Also grep the plan for accesses to private attributes (`._foo`) from OUTSIDE the owning module.

- **Rule — Sequential LLM dispatch at v1 scale (<20 items per stage) is the shipped shape; concurrent fan-out is deferred.** v1 cardinality is 7 total LLM calls per end-to-end run (1 version_diff + 1 citation_verify + 2 obligation_extraction + 3 mapping) — gpt-5.4-nano latency × 7 is well under a minute and diskcache makes warm runs <1s. Every call site uses `for item in items: model_caller.call(...)`, which is still cached, structured, and retried.
  - **Why:** at Strata v1 scale, cache hits and retries matter; concurrency doesn't move the needle. The ModelCaller surface deliberately does NOT expose a `fan_out_for_task` helper in v1 — the module stays small. When a stage's cardinality crosses ~100 items (TDD §3.2's hierarchical diff, §3.5's two-stage extraction) we'll add a dedicated fan-out helper on `ModelCaller` and refactor that one caller; adding it speculatively is YAGNI.
  - **How to check:** grep the plan for `for X in Y:` blocks that call `model_caller.call(` inside the loop body. Sequential is correct at v1 cardinality. Any plan proposing `fan_out_for_task` or `n_jobs` or `ThreadPoolExecutor` wrapping `model_caller.call` must either (a) demonstrate the stage crosses the ~100-item bar at v1, or (b) be rejected as scope creep.

- **Rule — Environment variables are ONLY for genuine per-machine platform data or deploy-time-injected secrets. Configuration lives in `bootstrap.json` / `DomainConfig` / structured config files, NOT in env vars.** Legitimate env-var uses: (a) OS-provided platform data the process can't discover itself (`HOME`, `PATH`, `HOSTNAME`); (b) genuine secrets injected at deploy time (`OPENAI_API_KEY`, `POSTGRES_PASSWORD`); (c) library-controlled knobs we cannot route around (`PYTORCH_JIT`, `PYTHONPATH`). Everything else — storage backend selection, DSN structure, tenant lists, service endpoints, feature toggles, hyperparameters, model paths, cache directories — lives in `bootstrap.json` / `DomainConfig` sub-models / `config/default_domain_config.json` / operational flags.
  - **Why:** env vars are a copy of config state that lives outside the schema-enforced source of truth; every env-var-shaped config lookup is a drift risk. `.env` doesn't exist in production, so anything that lives there is invisible to production entirely. `"secret://env/DATABASE_URL"` in a bootstrap file is a legitimate narrow escape hatch (indirects a secret through env; the CONFIG lives in bootstrap; only the SECRET flows through env) — general env-var config lookups are the anti-pattern.
  - **How to check:** grep the plan for `os.getenv(`, `os.environ[`, `os.environ.get(`, new keys in `.env` / `.env.example`, `${VAR:-default}` shell fallbacks in `docker-compose.yml`. Every hit must be either (a) a genuine platform / secret / library-knob, or (b) refactored into `bootstrap.json` / `DomainConfig`.

- **Rule — When a task's spec lives in a design doc, `Read` the doc verbatim at task-authoring time AND at plan time — never paraphrase from memory and trust the paraphrase downstream.** Applies to any doc that defines schemas, contracts, or event catalogs where verbatim shape matters: PRD.md, TDD.md, ARCHITECTURE.md, PROMPT_CONVENTIONS.md. At task-authoring time: transcribe field lists, allowed values, and semantic invariants verbatim with §-line citations. At plan time: Phase 1 Explore MUST include reading the design-doc section named in the task description; cross-check every claim BEFORE writing the plan. At act time: the design doc is the first item in `files_to_read` and gets read BEFORE any code writes.
  - **Why:** paraphrasing from memory and trusting the paraphrase has repeatedly produced plans with invented fields, wrong column names, and wrong event kinds. The failure mode is substituting mental model for source of truth.
  - **How to check:** grep the plan for `per ${DOC}` / `§` / verbatim-quoted section citations. For each doc referenced, confirm `files_to_read` includes the doc path. During plan-mode audit, confirm the plan file cites specific line numbers from the section (not just the section number) — the presence of line numbers is proof the doc was actually opened.

- **Rule — Anything that feeds an LLM prompt input, or composes any string used in a cache key, must be produced in a stable total-order-deterministic way.** Non-determinism vectors to eliminate: (1) Postgres `SELECT` without a full `ORDER BY` — row order across pool sessions and query plans is unspecified; add `ORDER BY <primary>, <tiebreak-to-uniqueness>` on every SELECT whose rows feed a rendered list, a dict-insertion-order iteration, or any tuple whose elements matter. (2) Python `sorted(list, key=lambda x: x.foo)` or `.sort(key=...)` with a single key that can tie — Python's stable sort falls back to INPUT order for ties, and input order is often the non-deterministic thing you were trying to normalize; always add a stable-unique secondary key (an id, hash, or `passage_id`-shaped stable value). (3) Iterating `dict.items()` / `dict.values()` on a dict populated from a non-ORDER-BY'd SELECT — insertion order = SELECT row order = shuffled. (4) `for row in cur.fetchall()` where the SELECT lacks `ORDER BY` and the loop appends to a list that gets rendered or hashed. This rule applies to every list-shaped input to any LLM prompt, every field aggregated into a `', '.join(...)` prompt line, every tuple attached to a Pydantic model that later renders into a prompt, and every string composed into a `hashlib.*` cache-key input.
  - **Why:** the LLM cache is a valuable, paid-for asset — cache-key drift from unstable ordering silently pays every re-run. In Strata specifically, a change-detection cache keyed off an unordered passage list would silently miss hits across runs.
  - **How to check:** grep the plan for `sorted(`, `.sort(`, `SELECT`, `ORDER BY`, `cur.fetchall(`, `cur.execute(`, `dict.items()`, `dict.values()`, `', '.join(`, `hashlib.`, any Pydantic field ending in `_ids: tuple[str, ...]` / `_names: tuple[str, ...]` / `_fields: tuple[str, ...]`. For each hit that touches a code path that eventually renders to an LLM prompt or a cache key: (a) SELECTs need `ORDER BY <columns>` where the trailing columns form a stable total order; (b) sorts need a secondary tiebreaker key (id, hash, position); (c) dict iterations need to happen over a dict populated from an ORDER-BY'd SELECT OR to explicitly `sorted(dict.items())`.

## Plan-mode compliance audit

Before every plan exits, walk the plan file against every rule in 'MANDATORY design compliance rules' above. For each rule, either confirm the plan doesn't touch its scope, or confirm every requirement is met. If any rule is violated in the current draft, revise the plan before exiting — do NOT ship a plan with a known violation and rely on the user to catch it in review. This applies to plans authored by me OR by any spawned Plan / Explore agent — every Plan / Explore agent prompt MUST include the compliance-rules section verbatim (or a link to it) and MUST run this audit.

Fast mechanical pass over the plan file — grep for each keyword group and inspect every hit:

- `scripts/main_`, `tests/test_.*import scripts` — tests-import-scripts check
- `cache_key`, `hash`, `sha256`, `hashlib` — cache-key-plaintext check
- `orjson.loads(`, `json.loads(`, `open(`, `.read_bytes(`, `.read_text(`, `DomainConfig(**`, `TaskModelConfig(**`, `._` (private attribute access from outside owning module) — encapsulation / reuse check
- `for ` … `model_caller.call`, `for passage in`, `for change in`, `for candidate in` inside a stage runner — sequential-at-v1-scale check (any plan proposing `fan_out_for_task` / `n_jobs` / `ThreadPoolExecutor` wrapping `model_caller.call` must demonstrate the stage crosses the ~100-item bar at v1 or be rejected as scope creep)
- `os.getenv(`, `os.environ[`, `os.environ.get(`, new keys in `.env` / `.env.example`, `${VAR:-default}` shell fallbacks in `docker-compose.yml` — env-vs-config check
- `per ${DOC}`, `§`, `PRD §`, `TDD §`, `verbatim`, and any design-doc filename referenced in the task description — doc-verbatim check (every doc-reference must have the doc path in `files_to_read` AND the plan must cite specific line numbers)
- `sorted(`, `.sort(`, `SELECT` / `ORDER BY`, `cur.fetchall(`, `cur.execute(`, `dict.items()`, `dict.values()`, `', '.join(`, `hashlib.`, Pydantic fields ending in `_ids: tuple[str, ...]` / `_names: tuple[str, ...]` / `_fields: tuple[str, ...]` — prompt-input ordering determinism check

## LLM use

- always use Structured Outputs (Pydantic models) from LLMs
- never use field validators on those models
- categorical fields in Pydantic response models must use `Literal` types, not bare `str` — e.g. `Literal['draft', 'final']` not `str`, `Literal['material', 'cosmetic', 'none']` not `str`
- always support a `--confirm_llm` flag on scripts that prints the full prompt, token count and allows for a 'y/n' input from the user.
- `--confirm_llm` works with sequential dispatch at v1 scale — each `model_caller.call` prints the prompt and prompts for y/n before firing. There is no `--n_jobs` flag in v1 because there is no fan-out helper (sequential loops cover v1 cardinality — see the sequential-dispatch rule above). When a future stage crosses the ~100-item fan-out bar and adds a helper, revisit this interaction.
- use diskcache to cache LLM outputs as soon as they are received (key off model, prompts input, max_tokens, tool_use)
- the LLM cache is a valuable, paid-for asset — any change that alters cache keys (key shape, hashing, the inputs that compose the key, or anything that invalidates existing entries) requires explicit, individual user approval before being made; never bundle such a change into a larger refactor
- ALL expensive inference tasks must cache by default when implemented — not just LLM calls. Any function that runs a neural network on inputs that may be re-queried (sentence encoders, classifier predictions, embedding computation) gets diskcache from day one. Cache key composition: include the model identity (checkpoint path or name), all inference-time hyperparameters that affect output, and the full input payload. Two-pass pattern: lookup misses, infer only misses, persist via `on_batch_complete` for interrupt resilience. Callers default to an enabled cache (with a sensible `.X_cache` path at project root); `cache_dir=None` is the opt-out. Add the cache dir to `.gitignore`.
- citation grounding is a verification step, not a prompt-engineering trick. Every claim-producing LLM task is paired with a post-generation verifier that confirms the cited passage actually contains text supporting the claim. The verifier is its own module, its own eval, and its own confidence score — failures escalate rather than silently ship.

## Git commits

- Write the commit message to a **unique, task-specific file** under `/tmp/` using the Write tool — e.g. `/tmp/commit_msg_citation_grounding.txt`, `/tmp/commit_msg_review.txt`. Do NOT reuse `/tmp/commit_msg.txt`: parallel Claude Code sessions share `/tmp/` and will clobber each other's commit messages. Naming convention: `commit_msg_<short-purpose>.txt`.
- Then `git commit -F <that-file>`
- Never use `$(cat <<EOF)` or subshell patterns — they trigger extra permission prompts
- Never use `git commit -m "..."` for multi-line messages — use the file approach

## Bash patterns

- Never use `$()` subshell in Bash tool commands — it triggers extra permission prompts. Run the inner command separately and use the result directly.
- Never use inline Python (`python -c "..."`) — write a small script or use an importable module.
- Never chain commands with `&&` when arguments contain quotes — triggers 'Command contains quoted characters in flag names' warning. Run each command as a separate Bash tool invocation.
