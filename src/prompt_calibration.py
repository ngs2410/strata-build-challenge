'''Shared taxonomies and rubrics that every LLM prompt injects verbatim.

Four reference blocks are built here once at import time and consumed by
multiple prompts across the codebase (DRY — a single source of truth so
`obligation_extraction` and `mapping` agree on what "operational" means,
and so every classifier that could pick "tightened" vs "loosened" uses
the same perspective rule):

  * `REGULATED_ENTITY_PERSPECTIVE` — the project's convention that any
    "burden heavier / lighter" judgment is reasoned from the regulated
    entity's compliance surface (aggregator / utility / hospital), NOT
    from the regulator's authority. Any prompt that uses "tightened" or
    "loosened" (currently just `version_diff`) or that reacts to those
    classifications (currently `mapping`) must include this note so the
    whole pipeline agrees on perspective. Baked into `CHANGE_TYPE_RUBRIC`
    automatically; also importable standalone for prompts that need the
    perspective note without the full rubric.
  * `OBLIGATION_TAXONOMY_REFERENCE` — the four `Obligation.obligation_type`
    `Literal` values (reporting / operational / procedural / financial)
    expanded into definitions + examples. Keeps the LLM aligned with the
    `Literal` set on `Obligation` and `ExtractedObligation` in config.py.
  * `CHANGE_TYPE_RUBRIC` — the five `Change.change_type` values
    (added / removed / tightened / loosened / cosmetic) with definitions,
    prefaced by `REGULATED_ENTITY_PERSPECTIVE`.
  * `CONFIDENCE_LEVEL_RUBRIC` — the 0–5 integer self-report scale used by
    every task that emits a `confidence` field, paired with the rule
    "over-confidence is worse than under-confidence" so low-confidence
    outputs reliably escalate (Strata invariant: the system fails to a
    reviewer, not to a guess).

The rubrics are built from source dicts (`OBLIGATION_TYPES`, `CHANGE_TYPES`,
`CONFIDENCE_LEVELS`) so adding a value is a dict edit, not a prose rewrite.
Any `Literal` list in config.py whose values appear here must be kept in
sync — grep both files when adding a new type. Any new rubric or taxonomy
that could introduce the same whose-perspective ambiguity should either
reuse `REGULATED_ENTITY_PERSPECTIVE` or add a sibling perspective constant
here, NOT re-define the rule inline in a prompt.
'''

OBLIGATION_TYPES: dict[str, dict[str, str]] = {
  'reporting': {
    'description': (
      'A submission the regulated entity must file with the regulator on a '
      'defined cadence or triggered event. Produces an artefact.'
    ),
    'example': (
      'Monthly Renewable Portfolio Standard compliance filing to the state PUC '
      'listing retired RECs by month.'
    ),
  },
  'operational': {
    'description': (
      'A continuous operating constraint the regulated entity must satisfy '
      'while running the regulated activity. Not a one-time submission.'
    ),
    'example': (
      "Maintain real-time telemetry from aggregated DER resources to the "
      'RTO while those resources are participating in wholesale markets.'
    ),
  },
  'procedural': {
    'description': (
      'A process the regulated entity must follow before, during, or after a '
      'triggered activity. Governs HOW an action is taken, not WHAT is '
      'reported.'
    ),
    'example': (
      'Register each DER with the RTO at least 60 days before its first '
      'market participation.'
    ),
  },
  'financial': {
    'description': (
      'An obligation that affects revenue, cost allocation, settlement, or '
      'market pricing — tariff provisions, charge formulas, settlement rules.'
    ),
    'example': (
      'Aggregator pays MISO a $0.05/MWh settlement fee on cleared aggregation '
      'offers.'
    ),
  },
}

REGULATED_ENTITY_PERSPECTIVE: str = (
  'Perspective rule. Strata interprets every regulatory change from the '
  'REGULATED ENTITY\'s perspective — the aggregator, utility, hospital, '
  'broker, etc. whose compliance burden the system tracks. When a rule '
  'changes, the question is ALWAYS "does the regulated entity now have '
  'to do more, less, or the same?" — NOT "does the regulator have more '
  'or less authority?"\n'
  '\n'
  'A regulator-side relaxation that creates de facto new burden on the '
  'regulated entity is a TIGHTENING of the entity\'s obligation. A '
  'regulator-side expansion that lifts a burden the entity previously '
  'faced is a LOOSENING. If the direction is still ambiguous after '
  'applying this rule, drop confidence to 3 and name the ambiguity in '
  'confidence_rationale.'
)

CHANGE_TYPES: dict[str, str] = {
  'added': (
    'A new obligation or clause present in the new version that did not '
    'exist in the prior version. Use when the regulated entity now has '
    'to do something they had no corresponding duty to do before — even '
    'if the new clause is grafted onto a passage that already existed. '
    'prior_citation must be null.'
  ),
  'removed': (
    'An obligation or clause that existed in the prior version and is '
    'gone in the new version. The regulated entity no longer has to do '
    'something they previously had to do. new_citation must be null.'
  ),
  'tightened': (
    "An existing obligation whose scope widened, threshold stricter, or "
    "compliance burden heavier FOR THE REGULATED ENTITY. Includes "
    "regulator-side changes (e.g. 'the RTO may now impose a minimum "
    "size') that create de facto new burden on the entity."
  ),
  'loosened': (
    "An existing obligation whose scope narrowed, threshold laxer, or "
    "compliance burden lighter FOR THE REGULATED ENTITY. Includes "
    "regulator-side changes that lift a burden the entity previously "
    "faced."
  ),
  'cosmetic': (
    'A re-wording, re-numbering, or re-formatting that preserves the '
    'obligation unchanged in substance. The regulated entity\'s '
    'compliance burden is identical before and after.'
  ),
}

CONFIDENCE_LEVELS: dict[int, str] = {
  5: 'Unambiguous — the cited passages state the conclusion verbatim.',
  4: 'Strongly supported — one short inferential step from the cited passages.',
  3: (
    'Reasonable — the cited passages support the conclusion but a reviewer '
    'might legitimately disagree.'
  ),
  2: 'Weak — surface signals point this way but the inference is thin.',
  1: 'Speculative — judgement call with limited anchor in the cited text.',
  0: 'Guess — no defensible anchor in the cited text; escalate.',
}


def _build_obligation_taxonomy() -> str:
  lines = [
    'Obligation taxonomy. Every ExtractedObligation.obligation_type must be '
    'one of the four values below. The description + example calibrate '
    'what each value covers.',
    '',
  ]
  for name, body in OBLIGATION_TYPES.items():
    lines.append(f'- {name}: {body["description"]}')
    lines.append(f'  Example: {body["example"]}')
  return '\n'.join(lines)


def _build_change_type_rubric() -> str:
  lines = [
    'Change-type rubric. Every detected Change.change_type must be one of '
    'the five values below.',
    '',
    REGULATED_ENTITY_PERSPECTIVE,
    '',
  ]
  for name, description in CHANGE_TYPES.items():
    lines.append(f'- {name}: {description}')
  return '\n'.join(lines)


def _build_confidence_rubric() -> str:
  lines = [
    'Confidence rubric (0-5 integer). Self-report your confidence in the '
    'conclusion AND set confidence_rationale naming which rubric level you '
    'picked and why. Over-confidence is a worse failure than under-confidence '
    '— when in doubt, drop one level.',
    '',
  ]
  for level in sorted(CONFIDENCE_LEVELS, reverse=True):
    lines.append(f'- {level}: {CONFIDENCE_LEVELS[level]}')
  return '\n'.join(lines)


OBLIGATION_TAXONOMY_REFERENCE: str = _build_obligation_taxonomy()
CHANGE_TYPE_RUBRIC: str = _build_change_type_rubric()
CONFIDENCE_LEVEL_RUBRIC: str = _build_confidence_rubric()
