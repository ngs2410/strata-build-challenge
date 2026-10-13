from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import structlog
from pydantic import BaseModel, ConfigDict

from src.backends.openai_backend import OpenAIBackend
from src.config import TaskModelConfig
from src.llm import CallSpec


class _MiniResponse(BaseModel):
  model_config = ConfigDict(extra='forbid')
  answer: str


_CALL_SPEC = CallSpec(purpose='p', acceptance_criteria='a', failure_modes='f')


def _task_config(model_identifier='openai://gpt-5.4-nano', max_tokens=4096):
  return TaskModelConfig(
    model_identifier=model_identifier,
    max_tokens=max_tokens,
    reasoning_effort='low',
    verbosity='low',
    included_inputs=(),
  )


def _ok_response(parsed, input_tokens=100, output_tokens=50):
  return SimpleNamespace(
    status='completed',
    output_parsed=parsed,
    usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    output=[],
  )


def _mock_client(response):
  client = MagicMock()
  client.responses.parse.return_value = response
  return client


def test_openai_backend_strips_uri_prefix_before_dispatch():
  canned = _MiniResponse(answer='hello')
  client = _mock_client(_ok_response(canned))
  backend = OpenAIBackend(client=client)
  backend.call(
    task_name='test_task',
    task_config=_task_config('openai://gpt-5.4-nano'),
    params={'input': [{'role': 'user', 'content': 'hi'}]},
    response_type=_MiniResponse,
    call_spec=_CALL_SPEC,
  )
  _, kwargs = client.responses.parse.call_args
  assert kwargs['model'] == 'gpt-5.4-nano'


def test_openai_backend_passes_structured_output_params():
  canned = _MiniResponse(answer='x')
  client = _mock_client(_ok_response(canned))
  backend = OpenAIBackend(client=client)
  backend.call(
    task_name='test_task',
    task_config=_task_config(max_tokens=1234),
    params={'input': [{'role': 'user', 'content': 'hi'}]},
    response_type=_MiniResponse,
    call_spec=_CALL_SPEC,
  )
  _, kwargs = client.responses.parse.call_args
  assert kwargs['text_format'] is _MiniResponse
  assert kwargs['max_output_tokens'] == 1234
  assert kwargs['reasoning'] == {'effort': 'low'}
  assert kwargs['text'] == {'verbosity': 'low'}


def test_openai_backend_returns_parsed_pydantic():
  canned = _MiniResponse(answer='from openai')
  client = _mock_client(_ok_response(canned))
  backend = OpenAIBackend(client=client)
  result = backend.call(
    task_name='t',
    task_config=_task_config(),
    params={'input': []},
    response_type=_MiniResponse,
    call_spec=_CALL_SPEC,
  )
  assert isinstance(result, _MiniResponse)
  assert result.answer == 'from openai'


def test_openai_backend_raises_on_incomplete_response():
  incomplete = SimpleNamespace(
    status='incomplete',
    output_parsed=None,
    usage=SimpleNamespace(input_tokens=100, output_tokens=0),
    output=[],
    incomplete_details='max_output_tokens hit',
  )
  client = _mock_client(incomplete)
  backend = OpenAIBackend(client=client)
  with pytest.raises(RuntimeError, match='incomplete'):
    backend.call(
      task_name='t',
      task_config=_task_config(),
      params={'input': []},
      response_type=_MiniResponse,
      call_spec=_CALL_SPEC,
    )


def test_openai_backend_raises_on_refusal():
  refusal_content = SimpleNamespace(refusal='I will not do that.')
  refusal_message = SimpleNamespace(content=[refusal_content])
  refused = SimpleNamespace(
    status='completed',
    output_parsed=None,
    usage=SimpleNamespace(input_tokens=100, output_tokens=10),
    output=[refusal_message],
  )
  client = _mock_client(refused)
  backend = OpenAIBackend(client=client)
  with pytest.raises(RuntimeError, match='I will not do that'):
    backend.call(
      task_name='t',
      task_config=_task_config(),
      params={'input': []},
      response_type=_MiniResponse,
      call_spec=_CALL_SPEC,
    )


def test_openai_backend_logs_token_usage_and_cost():
  canned = _MiniResponse(answer='x')
  client = _mock_client(_ok_response(canned, input_tokens=1000, output_tokens=500))
  backend = OpenAIBackend(client=client)
  with structlog.testing.capture_logs() as logs:
    backend.call(
      task_name='test_task',
      task_config=_task_config('openai://gpt-5.4-nano'),
      params={'input': []},
      response_type=_MiniResponse,
      call_spec=_CALL_SPEC,
    )
  call_events = [log for log in logs if log['event'] == 'openai_backend.call']
  assert len(call_events) == 1
  event = call_events[0]
  assert event['task_name'] == 'test_task'
  assert event['model_name'] == 'gpt-5.4-nano'
  assert event['input_tokens'] == 1000
  assert event['output_tokens'] == 500
  expected_cost = (
    Decimal(1000) * Decimal('0.00010') / Decimal(1000)
    + Decimal(500) * Decimal('0.00040') / Decimal(1000)
  ).quantize(Decimal('0.000001'))
  assert event['estimated_cost_usd'] == str(expected_cost)


def test_openai_backend_logs_warning_when_pricing_missing():
  canned = _MiniResponse(answer='x')
  client = _mock_client(_ok_response(canned))
  backend = OpenAIBackend(client=client)
  with structlog.testing.capture_logs() as logs:
    backend.call(
      task_name='t',
      task_config=_task_config('openai://gpt-9000-ultra'),
      params={'input': []},
      response_type=_MiniResponse,
      call_spec=_CALL_SPEC,
    )
  missing = [log for log in logs if log['event'] == 'openai_backend.pricing_missing']
  assert len(missing) == 1
  assert missing[0]['model_name'] == 'gpt-9000-ultra'
  call_events = [log for log in logs if log['event'] == 'openai_backend.call']
  assert call_events[0]['estimated_cost_usd'] is None
