from src.prompt_calibration import (
  CHANGE_TYPE_RUBRIC,
  CHANGE_TYPES,
  CONFIDENCE_LEVEL_RUBRIC,
  CONFIDENCE_LEVELS,
  OBLIGATION_TAXONOMY_REFERENCE,
  OBLIGATION_TYPES,
  REGULATED_ENTITY_PERSPECTIVE,
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
