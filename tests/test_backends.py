import pytest
from pydantic import BaseModel, ConfigDict

from src.backends.jev_backend import BACKEND_PREFIX as JEV_PREFIX
from src.backends.jev_backend import JevBackend
from src.backends.openai_backend import BACKEND_PREFIX as OPENAI_PREFIX
from src.backends.openai_backend import OpenAIBackend
from src.backends.stub_backend import BACKEND_PREFIX as STUB_PREFIX
from src.backends.stub_backend import StubBackend
from src.config import TaskModelConfig
from src.llm import CallSpec
from src.version_diff import VersionDiffResponse


def _task_config(model_identifier):
  return TaskModelConfig(
    model_identifier=model_identifier,
    max_tokens=1024,
    reasoning_effort='low',
    verbosity='low',
    included_inputs=(),
  )


_CALL_SPEC = CallSpec(purpose='p', acceptance_criteria='a', failure_modes='f')


def test_stub_backend_matches_only_stub_prefix():
  backend = StubBackend()
  assert backend.matches(f'{STUB_PREFIX}version_diff') is True
  assert backend.matches(f'{OPENAI_PREFIX}gpt-5') is False
  assert backend.matches(f'{JEV_PREFIX}jev-1') is False


def test_openai_backend_matches_only_openai_prefix():
  backend = OpenAIBackend()
  assert backend.matches(f'{OPENAI_PREFIX}gpt-5.4-nano') is True
  assert backend.matches(f'{STUB_PREFIX}x') is False


def test_jev_backend_matches_only_jev_prefix():
  backend = JevBackend()
  assert backend.matches(f'{JEV_PREFIX}jev-1.13.0') is True
  assert backend.matches(f'{STUB_PREFIX}x') is False


def test_openai_backend_call_raises_not_implemented():
  class _Unused(BaseModel):
    model_config = ConfigDict(extra='forbid')
    x: int

  with pytest.raises(NotImplementedError, match='follow-up commit'):
    OpenAIBackend().call(
      task_name='version_diff',
      task_config=_task_config('openai://gpt-5'),
      params={},
      response_type=_Unused,
      call_spec=_CALL_SPEC,
    )


def test_jev_backend_call_raises_not_implemented():
  class _Unused(BaseModel):
    model_config = ConfigDict(extra='forbid')
    x: int

  with pytest.raises(NotImplementedError, match='follow-up commit'):
    JevBackend().call(
      task_name='citation_verify',
      task_config=_task_config('jev://jev-1.13.0'),
      params={},
      response_type=_Unused,
      call_spec=_CALL_SPEC,
    )


def _three_passage_versions():
  from datetime import datetime, timezone

  from src.config import Passage, ProceedingVersion

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


def test_stub_backend_resolves_task_registered_stub_factory():
  nopr, final = _three_passage_versions()
  result = StubBackend().call(
    task_name='version_diff',
    task_config=_task_config('stub://version_diff'),
    params={'prior': nopr, 'final': final},
    response_type=VersionDiffResponse,
    call_spec=_CALL_SPEC,
  )
  assert isinstance(result, VersionDiffResponse)
  assert len(result.changes) == 3


def test_stub_backend_raises_on_response_type_mismatch():
  nopr, final = _three_passage_versions()

  class _WrongType(BaseModel):
    model_config = ConfigDict(extra='forbid')
    x: int

  with pytest.raises(TypeError, match='expected _WrongType'):
    StubBackend().call(
      task_name='version_diff',
      task_config=_task_config('stub://version_diff'),
      params={'prior': nopr, 'final': final},
      response_type=_WrongType,
      call_spec=_CALL_SPEC,
    )
