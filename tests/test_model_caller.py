from datetime import datetime, timezone

import pytest

from src.backends.openai_backend import BACKEND_PREFIX as BACKEND_OPENAI
from src.backends.stub_backend import BACKEND_PREFIX as BACKEND_STUB
from src.config import Passage, ProceedingVersion
from src.llm import CallSpec
from src.model_caller import ModelCaller
from src.version_diff import VersionDiffResponse

_CALL_SPEC = CallSpec(purpose='p', acceptance_criteria='a', failure_modes='f')


def _versions():
  def _version(status, version_id):
    return ProceedingVersion(
      tenant_id='verdant_grid',
      environment='prod',
      proceeding_id='proc',
      version_id=version_id,
      status=status,
      docket_number='D1',
      issued_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
      source_url='https://x',
      passages=(
        Passage(passage_id=f'proc:{version_id}:§1:p1', section_path=('§1',), position=1, text='t1'),
        Passage(passage_id=f'proc:{version_id}:§1:p2', section_path=('§1',), position=2, text='t2'),
        Passage(passage_id=f'proc:{version_id}:§1:p3', section_path=('§1',), position=3, text='t3'),
      ),
    )

  return _version('draft', 'nopr'), _version('final', 'final')


def test_resolve_backend_matches_prefix(verdant_grid_seeded):
  del verdant_grid_seeded
  caller = ModelCaller()
  openai_backend = caller._resolve_backend(f'{BACKEND_OPENAI}gpt-5')
  assert type(openai_backend).__name__ == 'OpenAIBackend'
  stub_backend = caller._resolve_backend(f'{BACKEND_STUB}version_diff')
  assert type(stub_backend).__name__ == 'StubBackend'


def test_resolve_backend_raises_on_unknown_prefix(verdant_grid_seeded):
  del verdant_grid_seeded
  caller = ModelCaller()
  with pytest.raises(ValueError, match='No backend matches'):
    caller._resolve_backend('unknown://something')


def test_call_dispatches_to_stub_and_returns_parsed_response(
  verdant_grid_seeded, isolated_model_cache
):
  del verdant_grid_seeded
  caller = ModelCaller(cache_dir=isolated_model_cache)
  prior, final = _versions()
  response = caller.call(
    task_name='version_diff',
    params={'input': [], 'inputs_dict': {}, 'prior': prior, 'final': final},
    response_type=VersionDiffResponse,
    call_spec=_CALL_SPEC,
    tenant_id='verdant_grid',
    environment='prod',
  )
  assert isinstance(response, VersionDiffResponse)
  assert len(response.changes) == 3


def test_call_second_invocation_hits_cache(verdant_grid_seeded, isolated_model_cache):
  del verdant_grid_seeded
  caller = ModelCaller(cache_dir=isolated_model_cache)
  prior, final = _versions()
  shared_params = {'input': [{'role': 'user', 'content': 'x'}], 'inputs_dict': {}}
  caller.call(
    task_name='version_diff',
    params={**shared_params, 'prior': prior, 'final': final},
    response_type=VersionDiffResponse,
    call_spec=_CALL_SPEC,
    tenant_id='verdant_grid',
    environment='prod',
  )
  assert caller._cache.get(_only_key(caller)) is not None
  cached_response = caller.call(
    task_name='version_diff',
    params={**shared_params, 'prior': prior, 'final': final},
    response_type=VersionDiffResponse,
    call_spec=_CALL_SPEC,
    tenant_id='verdant_grid',
    environment='prod',
  )
  assert isinstance(cached_response, VersionDiffResponse)
  assert len(cached_response.changes) == 3


def _only_key(caller):
  keys = list(caller._cache.iterkeys())
  assert len(keys) == 1
  return keys[0]


def test_cache_key_is_plaintext_json(verdant_grid_seeded, isolated_model_cache):
  del verdant_grid_seeded
  caller = ModelCaller(cache_dir=isolated_model_cache)
  prior, final = _versions()
  caller.call(
    task_name='version_diff',
    params={
      'input': [{'role': 'user', 'content': 'hello'}],
      'inputs_dict': {'passage_text': 'x'},
      'prior': prior,
      'final': final,
    },
    response_type=VersionDiffResponse,
    call_spec=_CALL_SPEC,
    tenant_id='verdant_grid',
    environment='prod',
  )
  key = _only_key(caller)
  assert 'stub://version_diff' in key
  assert '"model_identifier"' in key
  assert '"response_type":"VersionDiffResponse"' in key
  assert 'hello' in key


def test_set_confirm_llm_toggles_flag():
  caller = ModelCaller()
  assert caller._confirm_llm is False
  caller.set_confirm_llm(True)
  assert caller._confirm_llm is True
  caller.set_confirm_llm(False)
  assert caller._confirm_llm is False


def test_cache_key_changes_with_input(verdant_grid_seeded, isolated_model_cache):
  del verdant_grid_seeded
  caller = ModelCaller(cache_dir=isolated_model_cache)
  prior, final = _versions()
  for content in ('one', 'two'):
    caller.call(
      task_name='version_diff',
      params={
        'input': [{'role': 'user', 'content': content}],
        'inputs_dict': {},
        'prior': prior,
        'final': final,
      },
      response_type=VersionDiffResponse,
      call_spec=_CALL_SPEC,
      tenant_id='verdant_grid',
      environment='prod',
    )
  assert len(list(caller._cache.iterkeys())) == 2


def test_confirm_llm_prompts_and_proceeds_on_blank_input(
  verdant_grid_seeded, isolated_model_cache, monkeypatch, capsys
):
  del verdant_grid_seeded
  caller = ModelCaller(cache_dir=isolated_model_cache, confirm_llm=True)
  monkeypatch.setattr('builtins.input', lambda _prompt='': '')
  prior, final = _versions()
  response = caller.call(
    task_name='version_diff',
    params={
      'input': [{'role': 'user', 'content': 'confirm-test'}],
      'inputs_dict': {'passage_text': 'short passage'},
      'prior': prior,
      'final': final,
    },
    response_type=VersionDiffResponse,
    call_spec=_CALL_SPEC,
    tenant_id='verdant_grid',
    environment='prod',
  )
  assert isinstance(response, VersionDiffResponse)
  stdout = capsys.readouterr().out
  assert '[model_caller] task=version_diff' in stdout
  assert 'estimated_tokens=' in stdout
  assert 'Passage text=' in stdout  # the INPUT_LABELS mapping for passage_text


def test_confirm_llm_prompts_and_proceeds_on_lowercase_y(
  verdant_grid_seeded, isolated_model_cache, monkeypatch
):
  del verdant_grid_seeded
  caller = ModelCaller(cache_dir=isolated_model_cache, confirm_llm=True)
  monkeypatch.setattr('builtins.input', lambda _prompt='': 'y')
  prior, final = _versions()
  response = caller.call(
    task_name='version_diff',
    params={
      'input': [{'role': 'user', 'content': 'confirm-y'}],
      'inputs_dict': {},
      'prior': prior,
      'final': final,
    },
    response_type=VersionDiffResponse,
    call_spec=_CALL_SPEC,
    tenant_id='verdant_grid',
    environment='prod',
  )
  assert isinstance(response, VersionDiffResponse)


def test_confirm_llm_raises_runtime_error_on_decline(
  verdant_grid_seeded, isolated_model_cache, monkeypatch
):
  del verdant_grid_seeded
  caller = ModelCaller(cache_dir=isolated_model_cache, confirm_llm=True)
  monkeypatch.setattr('builtins.input', lambda _prompt='': 'n')
  prior, final = _versions()
  with pytest.raises(RuntimeError, match='declined version_diff'):
    caller.call(
      task_name='version_diff',
      params={
        'input': [{'role': 'user', 'content': 'confirm-n'}],
        'inputs_dict': {},
        'prior': prior,
        'final': final,
      },
      response_type=VersionDiffResponse,
      call_spec=_CALL_SPEC,
      tenant_id='verdant_grid',
      environment='prod',
    )
