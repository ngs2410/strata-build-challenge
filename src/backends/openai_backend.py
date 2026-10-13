'''OpenAI responses-API backend for `ModelCaller` — the `openai://` scheme.

Thin adapter over the openai SDK's `client.responses.parse(...)`. Called only
by `ModelCaller.call`; never from feature code directly. `matches` returns
True iff the task's `model_identifier` starts with `openai://`; the suffix is
the OpenAI model name (e.g. `openai://gpt-5.4-nano` → `gpt-5.4-nano`).

Dispatch sends:
  * `input`: the rendered prompt string from `params['input']`
  * `text_format`: the Pydantic response class, so OpenAI returns a
    structured object (CLAUDE.md: always use Structured Outputs; no field
    validators).
  * `max_output_tokens`, `reasoning.effort`, `text.verbosity` from
    `TaskModelConfig`.

Retry policy: exponential backoff on `RateLimitError`,
`InternalServerError`, `APIConnectionError` only — never on auth or 4xx
schema errors (which would just reproduce). `MAX_RETRIES = 3`,
`INITIAL_BACKOFF_SECONDS = 1.0`, `BACKOFF_MULTIPLIER = 2.0`.

Failure modes raised (never swallowed, per CLAUDE.md fail-fast rule):
  * `response.status == 'incomplete'` → RuntimeError with
    `incomplete_details` for debugging.
  * `response.output_parsed is None` → RuntimeError, surfacing any refusal
    text extracted from `response.output[0].content[0].refusal`.
  * Retryable exceptions exhausted → the final exception propagates.

Cost telemetry logged per call (`openai_backend.call`):
`{task_name, model_name, input_tokens, output_tokens, estimated_cost_usd}`
where the cost is computed from `_PRICING_USD_PER_1K_TOKENS` as `Decimal`
(hard-pinned per model id — update when the frontier model changes). An
unknown model logs `openai_backend.pricing_missing` and omits the cost
field; it does not block the call. CLAUDE.md "explicit unit economics".

The `OpenAI()` client is lazily constructed on first use so tests can inject
a stub via `OpenAIBackend(client=...)`.
'''

import time
from decimal import Decimal
from typing import TypeVar

from openai import APIConnectionError, InternalServerError, RateLimitError
from pydantic import BaseModel

from src.config import TaskModelConfig
from src.llm import CallSpec
from src.telemetry import get_logger

T = TypeVar('T', bound=BaseModel)

BACKEND_PREFIX = 'openai://'

RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
  RateLimitError,
  InternalServerError,
  APIConnectionError,
)
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0

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
    response = self._dispatch_with_retry(
      client, task_name, model_name, task_config, params, response_type
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

  def _dispatch_with_retry(
    self,
    client,
    task_name: str,
    model_name: str,
    task_config: TaskModelConfig,
    params: dict,
    response_type: type[T],
  ):
    delay = INITIAL_BACKOFF_SECONDS
    for attempt in range(MAX_RETRIES + 1):
      try:
        return client.responses.parse(
          model=model_name,
          input=params['input'],
          text_format=response_type,
          max_output_tokens=task_config.max_tokens,
          reasoning={'effort': task_config.reasoning_effort},
          text={'verbosity': task_config.verbosity},
        )
      except RETRYABLE_EXCEPTIONS as exc:
        if attempt == MAX_RETRIES:
          self._log.error(
            'openai_backend.retry_exhausted',
            task_name=task_name,
            model_name=model_name,
            attempt=attempt,
            error_type=type(exc).__name__,
          )
          raise
        self._log.warning(
          'openai_backend.retry',
          task_name=task_name,
          model_name=model_name,
          attempt=attempt,
          next_delay_seconds=delay,
          error_type=type(exc).__name__,
        )
        time.sleep(delay)
        delay *= BACKOFF_MULTIPLIER
    raise RuntimeError('unreachable')


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
