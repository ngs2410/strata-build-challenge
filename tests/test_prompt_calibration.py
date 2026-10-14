from src.prompt_calibration import (
  CHANGE_TYPE_RUBRIC,
  CHANGE_TYPES,
  CONFIDENCE_LEVEL_RUBRIC,
  CONFIDENCE_LEVELS,
  OBLIGATION_TAXONOMY_REFERENCE,
  OBLIGATION_TYPES,
  RECOMMENDED_ACTION_RUBRIC,
  RECOMMENDED_ACTIONS,
  REGULATED_ENTITY_PERSPECTIVE,
  ROUTING_DESTINATION_GLOSSARY,
  ROUTING_DESTINATIONS,
)


def test_obligation_types_cover_the_four_literal_values():
  assert set(OBLIGATION_TYPES) == {'reporting', 'operational', 'procedural', 'financial'}


def test_change_types_cover_the_five_literal_values():
  assert set(CHANGE_TYPES) == {'added', 'removed', 'tightened', 'loosened', 'cosmetic'}


def test_confidence_levels_cover_the_full_scale():
  assert set(CONFIDENCE_LEVELS) == {0, 1, 2, 3, 4, 5}


def test_obligation_taxonomy_reference_names_every_type_and_includes_examples():
  for name, body in OBLIGATION_TYPES.items():
    assert name in OBLIGATION_TAXONOMY_REFERENCE
    assert body['example'] in OBLIGATION_TAXONOMY_REFERENCE


def test_recommended_actions_match_mapping_literal():
  assert set(RECOMMENDED_ACTIONS) == {'review', 'amend', 'no_op'}


def test_recommended_action_rubric_names_every_action():
  for name in RECOMMENDED_ACTIONS:
    assert name in RECOMMENDED_ACTION_RUBRIC


def test_recommended_action_rubric_names_the_decision_rule():
  # Each action's description must say WHEN to pick it, not just WHAT it
  # means. Observed failure mode: without a decision rule the LLM picks
  # amend/review interchangeably based on surface tone of the change.
  assert 'match_confidence' in RECOMMENDED_ACTIONS['no_op']
  assert 'first-pass human read' in RECOMMENDED_ACTIONS['review']
  assert 'concrete update' in RECOMMENDED_ACTIONS['amend']
  # When uncertain, prefer review — the queue's safety default.
  assert 'prefer review' in RECOMMENDED_ACTIONS['amend']


def test_routing_destinations_match_mapping_literal():
  assert set(ROUTING_DESTINATIONS) == {'market_ops', 'legal', 'it_metering'}


def test_routing_destination_glossary_names_every_destination():
  for name in ROUTING_DESTINATIONS:
    assert name in ROUTING_DESTINATION_GLOSSARY


def test_routing_destination_glossary_defines_each_team_scope():
  # Each team must describe what it owns AND what it doesn't own. The
  # "Does NOT own" clause is the key signal — without it the LLM routes
  # by keyword match ("tariff" → legal, "DER" → market_ops, "meter" →
  # it_metering) without principled handoff.
  for description in ROUTING_DESTINATIONS.values():
    assert 'Owns' in description
    assert 'Does NOT own' in description


def test_obligation_taxonomy_reference_embeds_regulated_entity_perspective():
  # The extractor must not treat regulator-side duties ("the Commission
  # shall...", "the RTO shall...") as obligations of the regulated
  # entity. The shared perspective constant is baked into the taxonomy
  # so any prompt consuming it inherits the rule — no separate opt-in
  # required (same pattern as CHANGE_TYPE_RUBRIC).
  assert REGULATED_ENTITY_PERSPECTIVE in OBLIGATION_TAXONOMY_REFERENCE


def test_change_type_rubric_names_every_change_type():
  for name in CHANGE_TYPES:
    assert name in CHANGE_TYPE_RUBRIC


def test_confidence_rubric_includes_every_level_and_warns_against_overconfidence():
  for level in CONFIDENCE_LEVELS:
    assert f'- {level}:' in CONFIDENCE_LEVEL_RUBRIC
  assert 'Over-confidence' in CONFIDENCE_LEVEL_RUBRIC


def test_each_obligation_type_body_has_description_and_example():
  for body in OBLIGATION_TYPES.values():
    assert 'description' in body
    assert 'example' in body
    assert body['description']
    assert body['example']


def test_regulated_entity_perspective_names_the_rule_and_the_de_facto_tightening():
  # The perspective constant is the single reference for the "whose
  # perspective" ambiguity that bit version_diff on the Order 2222
  # minimum-size change. Any prompt that uses "tightened" or "loosened"
  # must either reuse CHANGE_TYPE_RUBRIC (which includes this) or
  # include the constant standalone.
  assert 'REGULATED ENTITY' in REGULATED_ENTITY_PERSPECTIVE
  assert 'de facto' in REGULATED_ENTITY_PERSPECTIVE
  assert 'TIGHTENING' in REGULATED_ENTITY_PERSPECTIVE
  assert 'LOOSENING' in REGULATED_ENTITY_PERSPECTIVE


def test_change_type_rubric_includes_regulated_entity_perspective():
  # The rubric must embed the perspective rule inline so a prompt that
  # includes CHANGE_TYPE_RUBRIC automatically teaches the LLM the
  # project convention — no separate opt-in required.
  assert REGULATED_ENTITY_PERSPECTIVE in CHANGE_TYPE_RUBRIC


def test_tightened_and_loosened_descriptions_name_the_regulated_entity():
  # The two change types whose direction depends on perspective must
  # name "the regulated entity" explicitly in their definitions —
  # the perspective rule must be visible at the point of classification,
  # not just at the top of the rubric.
  assert 'REGULATED ENTITY' in CHANGE_TYPES['tightened']
  assert 'REGULATED ENTITY' in CHANGE_TYPES['loosened']


def test_added_and_removed_descriptions_name_null_citation_sides():
  # The two "one side only" change types must say which citation is
  # null — this is the second observed failure mode (prior_citation
  # emitted on added changes).
  assert 'prior_citation must be null' in CHANGE_TYPES['added']
  assert 'new_citation must be null' in CHANGE_TYPES['removed']


def test_added_description_distinguishes_standalone_from_parameter_modification():
  # Third observed misclassification pattern: the model treated a new
  # 60-day timing clause ("§(B) 60 days before participation") as "added"
  # when the registration obligation it timed had existed before. The
  # `added` definition must explicitly rule this out — parameter
  # modifications are tightened/loosened, not added.
  assert 'STANDALONE' in CHANGE_TYPES['added']
  assert 'parameters' in CHANGE_TYPES['added']
  assert 'tightened' in CHANGE_TYPES['added']  # the pointer to the right type


def test_tightened_description_names_the_common_parameter_types():
  # The classifier needs concrete examples of what counts as a parameter
  # modification. Timing, data elements, thresholds, scope are the four
  # observed cases in the FERC / HIPAA / SEC domains Strata targets.
  tightened = CHANGE_TYPES['tightened']
  assert 'timing' in tightened
  assert 'data element' in tightened
  assert 'threshold' in tightened
  assert 'scope' in tightened


def test_tightened_and_loosened_name_the_grouping_rule():
  # Sub-modifications to the same obligation group into ONE change —
  # this is the fourth observed failure mode (two added changes for the
  # §(B) 60-day timing + point-of-interconnection).
  assert 'group' in CHANGE_TYPES['tightened'].lower()
  assert 'group' in CHANGE_TYPES['loosened'].lower()
