from src.llm import CallSpec, PromptSpec


def _spec(**overrides):
  base = dict(
    system_prompt='sys',
    task='do the thing',
    inputs_dict={'passage_text': 'the passage'},
  )
  base.update(overrides)
  return PromptSpec(**base)


def test_prompt_spec_render_omits_none_sections():
  spec = _spec(steps=None, details=None, rubrics=None, pitfalls=None, field_intents=None)
  system, user = spec.render()
  assert system == 'sys'
  assert '## Task' in user
  assert '## Steps' not in user
  assert '## Rubrics' not in user


def test_prompt_spec_render_includes_present_sections():
  spec = _spec(steps='1. Read.\n2. Think.', rubrics='- Be exact.')
  _, user = spec.render()
  assert '## Steps\n1. Read.\n2. Think.' in user
  assert '## Rubrics\n- Be exact.' in user


def test_prompt_spec_render_filters_inputs_by_included_inputs():
  spec = PromptSpec(
    system_prompt='sys',
    task='t',
    inputs_dict={'passage_text': 'A', 'surrounding_context': 'B'},
  )
  _, user = spec.render(included_inputs=('passage_text',))
  assert 'Passage text: A' in user
  assert 'B' not in user


def test_prompt_spec_render_includes_all_inputs_when_none_specified():
  spec = PromptSpec(
    system_prompt='sys',
    task='t',
    inputs_dict={'passage_text': 'A', 'surrounding_context': 'B'},
  )
  _, user = spec.render()
  assert 'Passage text: A' in user
  assert 'Surrounding context: B' in user


def test_prompt_spec_render_uses_input_labels_for_prefix():
  spec = _spec(inputs_dict={'claim': 'something must happen'})
  _, user = spec.render()
  assert 'Claim: something must happen' in user


def test_prompt_spec_render_skips_empty_input_values():
  spec = PromptSpec(
    system_prompt='sys',
    task='t',
    inputs_dict={'passage_text': 'A', 'surrounding_context': ''},
  )
  _, user = spec.render()
  assert 'Passage text: A' in user
  assert 'Surrounding context' not in user


def test_prompt_spec_render_appends_json_example_last():
  spec = _spec(json_example='{"foo": "<bar>"}')
  _, user = spec.render()
  assert user.endswith('## JSON Output Example\n{"foo": "<bar>"}')


def test_call_spec_is_immutable():
  import dataclasses

  spec = CallSpec(purpose='p', acceptance_criteria='a', failure_modes='f')
  assert spec.purpose == 'p'
  import pytest

  with pytest.raises(dataclasses.FrozenInstanceError):
    spec.purpose = 'x'
