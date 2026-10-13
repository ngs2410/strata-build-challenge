'''Deterministic stub backend for the `stub://` scheme — the no-network path.

Used by tests, `--dry_run` script invocations, and any environment where
spending LLM budget is not wanted. The adapter looks up the per-task stub
in `PROMPT_REGISTRY` (every registered task declares a
`stub_module` / `stub_function` pair) and calls it with the same `params`
dict a real backend would receive. Stubs return a fully constructed
`<Task>Response` Pydantic instance.

A tenant/env swaps to the stub backend by setting the task's
`model_identifier` to `stub://...` in `DomainConfig.task_model_configs` —
no code change required.

The returned object is `isinstance`-checked against `response_type`; a
mismatch raises `TypeError` immediately so a broken stub cannot pollute the
diskcache with the wrong shape.
'''

from typing import TypeVar

from pydantic import BaseModel

from src.config import TaskModelConfig
from src.llm import CallSpec
from src.prompt_registry import PROMPT_REGISTRY, resolve

T = TypeVar('T', bound=BaseModel)

BACKEND_PREFIX = 'stub://'


class StubBackend:
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
    del task_config, call_spec
    entry = PROMPT_REGISTRY[task_name]
    stub_fn = resolve(entry.stub_module, entry.stub_function)
    response = stub_fn(params)
    if not isinstance(response, response_type):
      raise TypeError(
        f'stub for {task_name} returned {type(response).__name__}; '
        f'expected {response_type.__name__}'
      )
    return response
