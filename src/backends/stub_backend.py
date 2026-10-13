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
