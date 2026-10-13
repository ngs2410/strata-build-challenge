from src.prompt_calibration import (
  CHANGE_TYPE_RUBRIC,
  CHANGE_TYPES,
  CONFIDENCE_LEVEL_RUBRIC,
  CONFIDENCE_LEVELS,
  OBLIGATION_TAXONOMY_REFERENCE,
  OBLIGATION_TYPES,
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
