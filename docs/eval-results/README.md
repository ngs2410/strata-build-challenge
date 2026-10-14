# Strata eval results — reading guide

Each `eval-results-<YYYY-MM-DD>.md` in this directory is an auto-
generated snapshot produced by `make eval-report`. The reports are
dated so a reviewer can see how metrics shift as prompts, gold, and
schemas change. This README explains how to read them.

## What the stages measure

- **change_detection (version_diff)** — did we detect the same
  changes gold labels? Match key is
  `(change_type, prior_passage_position, new_passage_position)`.
- **citation_verify** — of the predicted changes that matched a
  gold triple, how many had the right `verified` boolean on each
  side (prior / new)?
- **obligation_extraction** — precision/recall over the set of
  `(version_id, passage_position)` pairs; type_accuracy is the
  per-TP fraction with the right `obligation_type` Literal.
- **mapping** — precision/recall over the set of (item_type,
  item_id) pairs across all predicted mappings; action_accuracy
  and route_accuracy are the per-TP fraction with the right
  `proposed_action` / `proposed_route`.

## How to read the mapping action/route accuracies

They're denominated on **true positives only** — items the model
emitted AND gold expected. The denominator shrinks when recall is
low. A 4/9 action accuracy at mapping-recall=0.562 means "of the 9
items we emitted that gold also expected, 4 had the right proposed
action." The 5 missed-by-model items (FNs) don't count toward
action_accuracy because there was no model proposal to score.

## Known gaps baked into current prompts

Three deliberate conservatism choices show up in the mapping
numbers:

1. **Match-confidence floor at 3** (`MAPPING_CONFIDENCE_RUBRIC` in
   `src/prompt_calibration.py`). The prompt instructs the LLM to
   OMIT any candidate it would otherwise emit at confidence 1-2.
   Trades mapping recall for queue-noise reduction (Rachel's time
   dominates — per PRD §4, precision beats recall at this stage).
2. **Hedge-verb detector pitfall** on `match_rationale` (commit
   `56f9fca`). The prompt rejects candidates whose rationale leads
   with "may require", "should reflect", "likely impacts", "could
   affect". Also costs recall by design.
3. **Per-item action is a judgment call that nano gets wrong more
   often than it gets wrong on route.** `action_accuracy` lagging
   `route_accuracy` is a known pattern (routes track item type;
   actions require a thinner read of the Change × item interaction).
   Mitigation: a larger model tier on the mapping task's
   `model_identifier` would narrow this gap.

## Known gaps at the obligation-extraction stage

- **Type-boundary disagreements** on §(C) "the aggregator shall
  provide information sufficient to confirm capability" — gold
  labels this `operational` (continuous provision); the shipped
  prompt pitfall warns against `confidence=5` on `operational` vs
  `procedural` vs `reporting` type-boundary cases, and gpt-5.4-nano
  consistently picks `procedural`. One `type_correct=4/5` per
  version is this exact case.
- **One FP** sometimes appears when the model extracts an §(A)
  obligation gold doesn't expect (the regulator-side-filter
  teaching in commits `871a418`, `bedb038` is partial — the model
  occasionally emits a borderline §A item at nano scale).

## What these numbers do NOT measure

- **Rachel's actual satisfaction with the queue.** Mapping F1=0.692
  is a proxy; the real signal is whether she can triage items
  faster than her current workflow. That's a Phase 2 evaluation
  with a real user.
- **End-to-end latency / cost** at production scale. The §8.9 TDD
  open question (cross-tenant amortisation + multi-tier mapping
  cull) is where this stops being linear.
- **Model drift** — gpt-5.4-nano outputs can shift across OpenAI
  model-version updates. Pin the model id and re-run the eval
  after a known provider update.
