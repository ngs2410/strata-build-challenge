'''Single chokepoint for every LLM call — dispatch, cache, retries, telemetry.

`ModelCaller.call(task_name, params, response_type, call_spec, tenant_id,
environment)` is the one entrypoint every caller uses. It loads
`DomainConfig` for `(tenant_id, environment)`, looks up the
`TaskModelConfig` for `task_name`, resolves the backend via the URL-scheme
prefix (`openai://`, `jev://`, `stub://`), checks the diskcache, dispatches
if miss, writes the parsed Pydantic result back to cache, and returns it.

Backend facade (`Backend` Protocol): `matches(model_identifier) -> bool` +
`call(...)`. Backends are tried in order and the first `matches` wins — the
URL-scheme prefix is the full dispatch signal (CLAUDE.md: "dispatch by
URL-scheme prefix goes through a facade ABC — never an inline
if-startswith ladder in the caller"). Three concretes today: `StubBackend`
(deterministic for tests + dry-runs), `OpenAIBackend` (real responses API),
`JevBackend` (shell for TypeSafe AI, raises `NotImplementedError`).

Cache contract (CLAUDE.md invariant — "the LLM cache is a valuable, paid-for
asset"):
  * Key is a plaintext JSON dict with sorted keys, NOT a hash. Any change
    to the key shape or hashing requires explicit user approval (hashed
    keys cannot be renamed in place and expensive calls would get repaid
    for cosmetic changes).
  * Key composition: `{model_identifier, max_tokens, reasoning_effort,
    verbosity, response_type.__name__, params['input'], params['inputs_dict']}`.
  * `.model_cache/` on disk (in `.gitignore`), diskcache-backed, replayable
    with zero infra.

`confirm_llm` support: when enabled, prints the fully rendered prompt +
estimated token count + input summary to stdout and blocks for operator
`y/n` before dispatching. A `--confirm_llm` CLI flag flips this on;
`fan_out_for_task` concurrency behavior around confirm is TDD §4.1 and
LLM-rules.

The module exports a process-wide singleton `model_caller = ModelCaller()`
so `from src.model_caller import model_caller` is the standard import.
Call-site code must NEVER construct its own `OpenAI()` client or call the
openai SDK directly (CLAUDE.md: no parallel integrations).
'''

from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

import diskcache
import orjson
from pydantic import BaseModel

from src.config import TaskModelConfig, load_domain_config
from src.llm import INPUT_LABELS, CallSpec
from src.telemetry import get_logger

T = TypeVar('T', bound=BaseModel)

FACADE_NAME = 'model_caller'

MODEL_CACHE_DIR = Path('.model_cache')


@runtime_checkable
class Backend(Protocol):
  def matches(self, model_identifier: str) -> bool: ...

  def call(
    self,
    task_name: str,
    task_config: TaskModelConfig,
    params: dict,
    response_type: type[T],
    call_spec: CallSpec,
  ) -> T: ...


def _default_backends() -> list[Backend]:
  from src.backends.jev_backend import JevBackend
  from src.backends.openai_backend import OpenAIBackend
  from src.backends.stub_backend import StubBackend

  return [StubBackend(), OpenAIBackend(), JevBackend()]


class ModelCaller:
  def __init__(
    self,
    backends: list[Backend] | None = None,
    cache_dir: Path | None = None,
    confirm_llm: bool = False,
  ):
    self._backends = backends if backends is not None else _default_backends()
    self._cache = diskcache.Cache(str(cache_dir or MODEL_CACHE_DIR))
    self._confirm_llm = confirm_llm
    self._log = get_logger()

  def set_confirm_llm(self, confirm_llm: bool) -> None:
    self._confirm_llm = confirm_llm

  def call(
    self,
    task_name: str,
    params: dict,
    response_type: type[T],
    call_spec: CallSpec,
    tenant_id: str,
    environment: str,
  ) -> T:
    config = load_domain_config(tenant_id, environment)
    task_config = config.task_model_configs[task_name]
    backend = self._resolve_backend(task_config.model_identifier)
    cache_key_json = self._cache_key(task_config, params, response_type)
    cached = self._cache.get(cache_key_json)
    if cached is not None:
      self._log.info(
        'model_caller.cache_hit',
        task_name=task_name,
        model_identifier=task_config.model_identifier,
        tenant_id=tenant_id,
        environment=environment,
      )
      return response_type.model_validate_json(cached)
    if self._confirm_llm:
      self._prompt_user(task_name, task_config, params)
    parsed = backend.call(task_name, task_config, params, response_type, call_spec)
    self._cache.set(cache_key_json, parsed.model_dump_json())
    self._log.info(
      'model_caller.call',
      task_name=task_name,
      model_identifier=task_config.model_identifier,
      tenant_id=tenant_id,
      environment=environment,
      cache_hit=False,
    )
    return parsed

  def _resolve_backend(self, model_identifier: str) -> Backend:
    for backend in self._backends:
      if backend.matches(model_identifier):
        return backend
    raise ValueError(f'No backend matches model_identifier: {model_identifier}')

  def _cache_key(self, task_config: TaskModelConfig, params: dict, response_type: type[T]) -> str:
    key_dict = {
      'model_identifier': task_config.model_identifier,
      'max_tokens': task_config.max_tokens,
      'reasoning_effort': task_config.reasoning_effort,
      'verbosity': task_config.verbosity,
      'response_type': response_type.__name__,
      'input': params['input'],
      'inputs_dict': params['inputs_dict'],
    }
    return orjson.dumps(key_dict, option=orjson.OPT_SORT_KEYS).decode()

  def _prompt_user(self, task_name: str, task_config: TaskModelConfig, params: dict) -> None:
    rendered = params['input']
    estimated_tokens = len(str(rendered)) // 4
    inputs_dict = params['inputs_dict']
    inputs_summary = ', '.join(
      f'{INPUT_LABELS[k]}={len(str(v))}c' for k, v in inputs_dict.items()
    )
    print(f'\n{"-" * 70}')
    print(f'[model_caller] task={task_name} model={task_config.model_identifier}')
    print(f'[model_caller] estimated_tokens={estimated_tokens} inputs=({inputs_summary})')
    print('[model_caller] prompt:')
    for message in rendered:
      print(f'\n--- {message["role"]} ---')
      print(message['content'])
    print(f'\n{"-" * 70}')
    answer = input('[model_caller] Proceed? (y/n): ').strip().lower()
    if answer != 'y' and answer != '':
      raise RuntimeError(f'operator declined {task_name} at --confirm_llm prompt')


model_caller = ModelCaller()
