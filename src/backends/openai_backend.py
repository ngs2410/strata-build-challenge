from decimal import Decimal
from typing import TypeVar

from pydantic import BaseModel

from src.config import TaskModelConfig
from src.llm import CallSpec
from src.telemetry import get_logger

T = TypeVar('T', bound=BaseModel)

BACKEND_PREFIX = 'openai://'

_PRICING_USD_PER_1K_TOKENS: dict[str, dict[str, Decimal]] = {
  'gpt-5.4-nano': {
    'input': Decimal('0.00010'),
    'output': Decimal('0.00040'),
  },
}


class OpenAIBackend:
  def __init__(self, client=None):
    self._client = client
    self._log = get_logger()

  def matches(self, model_identifier: str) -> bool:
    return model_identifier.startswith(BACKEND_PREFIX)

  def call(
    self,
    task_name: str,
    task_config: TaskModelConfig,
    params: dict,
    response_type: type[T],
    call_spec: CallSpec,
  ) -> T:
    del call_spec
    model_name = task_config.model_identifier.removeprefix(BACKEND_PREFIX)
    client = self._get_client()
    response = client.responses.parse(
      model=model_name,
      input=params['input'],
      text_format=response_type,
      max_output_tokens=task_config.max_tokens,
      reasoning={'effort': task_config.reasoning_effort},
      text={'verbosity': task_config.verbosity},
    )
    if getattr(response, 'status', None) == 'incomplete':
      raise RuntimeError(
        f'OpenAI response incomplete for {task_name} '
        f'(model={model_name}, max_tokens={task_config.max_tokens}): '
        f'reason={getattr(response, "incomplete_details", "unknown")}'
      )
    parsed = response.output_parsed
    if parsed is None:
      refusal = _extract_refusal(response)
      raise RuntimeError(
        f'OpenAI returned no parsed output for {task_name} '
        f'(model={model_name}): refusal={refusal!r}'
      )
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost = _estimate_cost(model_name, input_tokens, output_tokens)
    self._log.info(
      'openai_backend.call',
      task_name=task_name,
      model_name=model_name,
      input_tokens=input_tokens,
      output_tokens=output_tokens,
      estimated_cost_usd=str(cost) if cost is not None else None,
    )
    return parsed

  def _get_client(self):
    if self._client is None:
      from openai import OpenAI

      self._client = OpenAI()
    return self._client


def _extract_refusal(response) -> str | None:
  output = getattr(response, 'output', None)
  if not output:
    return None
  first = output[0]
  content = getattr(first, 'content', None)
  if not content:
    return None
  return getattr(content[0], 'refusal', None)


def _estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> Decimal | None:
  pricing = _PRICING_USD_PER_1K_TOKENS.get(model_name)
  if pricing is None:
    get_logger().warning('openai_backend.pricing_missing', model_name=model_name)
    return None
  return (
    Decimal(input_tokens) * pricing['input'] / Decimal(1000)
    + Decimal(output_tokens) * pricing['output'] / Decimal(1000)
  ).quantize(Decimal('0.000001'))
