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

CHANGE_TYPES: dict[str, str] = {
  'added': (
    'A new obligation or clause present in the new version that did not '
    'exist in the prior version.'
  ),
  'removed': (
    'An obligation or clause that existed in the prior version and is gone '
    'in the new version.'
  ),
  'tightened': (
    'An existing obligation whose scope widened, threshold stricter, or '
    'compliance burden heavier.'
  ),
  'loosened': (
    'An existing obligation whose scope narrowed, threshold laxer, or '
    'compliance burden lighter.'
  ),
  'cosmetic': (
    'A re-wording, re-numbering, or re-formatting that preserves the '
    'obligation unchanged in substance.'
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
