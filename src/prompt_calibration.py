'''Shared taxonomies and rubrics that every LLM prompt injects verbatim.

Four reference blocks are built here once at import time and consumed by
multiple prompts across the codebase (DRY — a single source of truth so
`obligation_extraction` and `mapping` agree on what "operational" means,
and so every classifier that could pick "tightened" vs "loosened" uses
the same perspective rule):

  * `REGULATED_ENTITY_PERSPECTIVE` — the project's convention that any
    "burden heavier / lighter" judgment, AND the question of whose
    duties are tracked at all, is reasoned from the regulated entity's
    compliance surface (aggregator / utility / hospital), NOT from the
    regulator's authority. Any prompt that classifies "tightened" vs
    "loosened" or that enumerates obligations must include this note
    so the whole pipeline agrees on perspective. Baked into both
    `CHANGE_TYPE_RUBRIC` and `OBLIGATION_TAXONOMY_REFERENCE`
    automatically; also importable standalone.
  * `OBLIGATION_TAXONOMY_REFERENCE` — the four `Obligation.obligation_type`
    `Literal` values (reporting / operational / procedural / financial)
    expanded into definitions + examples, prefaced by
    `REGULATED_ENTITY_PERSPECTIVE` so the extractor never treats a
    regulator-side duty ("the Commission shall...", "the RTO shall...")
    as a regulated-entity obligation. Keeps the LLM aligned with the
    `Literal` set on `Obligation` and `ExtractedObligation` in config.py.
  * `CHANGE_TYPE_RUBRIC` — the five `Change.change_type` values
    (added / removed / tightened / loosened / cosmetic) with definitions,
    prefaced by `REGULATED_ENTITY_PERSPECTIVE`.
  * `CONFIDENCE_LEVEL_RUBRIC` — the 0–5 integer self-report scale used by
    tasks that emit a `confidence` field grounded in cited passages
    (version_diff, obligation_extraction, citation_verify). Paired with
    the rule "over-confidence is worse than under-confidence" so
    low-confidence outputs reliably escalate (Strata invariant: the
    system fails to a reviewer, not to a guess).
  * `MAPPING_CONFIDENCE_RUBRIC` — the mapping-specific 0–5 scale used by
    `mapping`. Semantic-overlap confidence rather than citation support,
    with an explicit emission floor at 3 (candidates that would score
    2 or below are omitted rather than hedged — keeps Rachel's queue
    noise-free).
  * `RECOMMENDED_ACTION_RUBRIC` — the three `Mapping.recommended_action`
    `Literal` values (no_op / review / amend) with the decision rule
    for picking each. Shared so a future rerouter or triage prompt
    doesn't re-define.
  * `ROUTING_DESTINATION_GLOSSARY` — the three
    `Mapping.routing_destination` `Literal` values (market_ops / legal /
    it_metering) with the ownership scope of each team. Prevents the
    LLM from routing by keyword match alone.

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
    'A wholly-NEW STANDALONE obligation present in the new version that '
    'did not exist in the prior version. The regulated entity now has to '
    'perform an ACTION they had no corresponding duty to perform before. '
    'An added obligation COULD stand alone even when it is written into '
    'the same paragraph as other obligations. Does NOT apply when a new '
    'clause modifies the parameters (timing, required data elements, '
    'thresholds, scope) of an EXISTING obligation — those are tightened '
    'or loosened. prior_citation must be null.'
  ),
  'removed': (
    'An obligation or clause that existed in the prior version and is '
    'gone in the new version. The regulated entity no longer has to do '
    'something they previously had to do. new_citation must be null.'
  ),
  'tightened': (
    'An existing obligation whose parameters got stricter FOR THE '
    'REGULATED ENTITY — common cases: adding a timing requirement '
    "(e.g. '... within 30 days'), adding a required data element to a "
    "reporting list (e.g. '... including the point of interconnection'), "
    'lowering a threshold, widening scope of who/what is covered, '
    'adding a precondition that must be satisfied. Also includes '
    "regulator-side changes (e.g. 'the RTO may now impose a minimum "
    "size') that create de facto new burden on the entity. If multiple "
    "parameters tighten the SAME underlying obligation, group them into "
    'ONE change (see Additional rules in the task prompt).'
  ),
  'loosened': (
    'An existing obligation whose parameters got laxer FOR THE '
    'REGULATED ENTITY — relaxed timing, dropped required data elements, '
    'raised threshold, narrowed scope, removed precondition. Also '
    'includes regulator-side changes that lift a burden the entity '
    'previously faced. If multiple parameters loosen the SAME '
    'underlying obligation, group them into ONE change.'
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

MAPPING_CONFIDENCE_LEVELS: dict[int, str] = {
  5: (
    'Unambiguous — the change explicitly names the item or an identifier '
    'the item carries (e.g., the change text mentions "Form X-17A-5" and '
    'the candidate is the Form X-17A-5 filing obligation).'
  ),
  4: (
    'Strongly supported — the change targets a parameter / scope / '
    'content that the item directly covers, one short inferential step '
    'from the change text (e.g., change tightens a filing deadline, '
    'candidate obligation IS that filing).'
  ),
  3: (
    'Reasonable — the item is substantively affected by the change but '
    'a reviewer might legitimately disagree about scope (e.g., change '
    "affects 'DER aggregation operations', candidate project is a "
    'specific DER aggregation program).'
  ),
  2: (
    'Weak — topically adjacent but the match depends on an inference '
    'the change text does not clearly support. Below the floor for '
    'emission: omit the candidate entirely rather than emitting a '
    'match_confidence=2 hedge.'
  ),
  1: (
    'Speculative — surface keyword overlap with no substantive effect. '
    'Below the floor for emission.'
  ),
  0: (
    'Guess — no defensible overlap. Below the floor for emission; '
    'escalate if uncertain.'
  ),
}


def _build_obligation_taxonomy() -> str:
  lines = [
    'Obligation taxonomy. Every ExtractedObligation.obligation_type must be '
    'one of the four values below. The description + example calibrate '
    'what each value covers.',
    '',
    REGULATED_ENTITY_PERSPECTIVE,
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


def _build_mapping_confidence_rubric() -> str:
  lines = [
    'Mapping confidence rubric (0-5 integer). Applies to both the overall '
    'mapping confidence AND to each affected_items[*].match_confidence. '
    'Match_confidence floor for emission is 3 — candidates that would '
    'score 2 or below are omitted entirely rather than hedged. '
    'Over-confidence is a worse failure than under-confidence.',
    '',
  ]
  for level in sorted(MAPPING_CONFIDENCE_LEVELS, reverse=True):
    lines.append(f'- {level}: {MAPPING_CONFIDENCE_LEVELS[level]}')
  return '\n'.join(lines)


OBLIGATION_TAXONOMY_REFERENCE: str = _build_obligation_taxonomy()
CHANGE_TYPE_RUBRIC: str = _build_change_type_rubric()
CONFIDENCE_LEVEL_RUBRIC: str = _build_confidence_rubric()
MAPPING_CONFIDENCE_RUBRIC: str = _build_mapping_confidence_rubric()


RECOMMENDED_ACTIONS: dict[str, str] = {
  'no_op': (
    "Nothing in the candidate set is substantively affected. The change is "
    "topically relevant but no obligation, project, or document in the "
    "company context requires any update. Choose when match_confidence on "
    "every candidate would be 2 or lower (and those matches are therefore "
    "omitted entirely)."
  ),
  'review': (
    "At least one candidate is substantively affected AND a first-pass "
    "human read is needed to decide WHAT to update. Choose when the "
    "affected item is clear but the required edit is a judgment call "
    "(e.g., 'tariff needs an update but Rachel needs to decide which "
    "sections')."
  ),
  'amend': (
    "At least one candidate is substantively affected AND there is a known "
    "concrete update (e.g., add a new required field to a form template, "
    "edit a specific tariff section, update an obligation parameter). "
    "Choose when the required edit is directly implied by the change text "
    "itself. When uncertain between review and amend, prefer review."
  ),
}

ROUTING_DESTINATIONS: dict[str, str] = {
  'market_ops': (
    "Market participation and operational workflows. Owns changes that "
    "affect wholesale market bidding, dispatch, settlement calendars, "
    "size-eligibility thresholds, participation registration, and "
    "day-ahead / real-time operational cadence. Does NOT own external "
    "regulatory filings or policy documents."
  ),
  'legal': (
    "External regulatory submissions and policy. Owns changes that affect "
    "tariff filings, compliance plans, external forms (SEC, FERC, state "
    "PUC), external training requirements, and policy documents. Does NOT "
    "own operational workflows or metering."
  ),
  'it_metering': (
    "Telemetry, metering, and operational data pipelines. Owns changes "
    "that affect data feeds, meter configuration, telemetry cadence, "
    "data-element specifications in operational submissions, and the "
    "underlying systems that produce them. Does NOT own commercial market "
    "operations or external filings."
  ),
}


def _build_recommended_action_rubric() -> str:
  lines = [
    'Recommended-action rubric. Pick exactly one.',
    '',
  ]
  for name, description in RECOMMENDED_ACTIONS.items():
    lines.append(f'- {name}: {description}')
  return '\n'.join(lines)


def _build_routing_destination_glossary() -> str:
  lines = [
    'Routing-destination glossary. Pick the single PRIMARY team '
    'accountable for the change (multi-destination routing is a known '
    'v2 extension — TDD §8.6).',
    '',
  ]
  for name, description in ROUTING_DESTINATIONS.items():
    lines.append(f'- {name}: {description}')
  return '\n'.join(lines)


RECOMMENDED_ACTION_RUBRIC: str = _build_recommended_action_rubric()
ROUTING_DESTINATION_GLOSSARY: str = _build_routing_destination_glossary()
