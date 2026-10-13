from typing import TypeVar

from pydantic import BaseModel

from src.config import TaskModelConfig
from src.llm import CallSpec

T = TypeVar('T', bound=BaseModel)

BACKEND_PREFIX = 'jev://'


class JevBackend:
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
    del task_name, task_config, params, response_type, call_spec
    raise NotImplementedError(
      'JevBackend is a shell in v1 — a follow-up commit will wire real Jev '
      '(Noul / Choice / Score) dispatch, likely LLM-emulated via OpenAI '
      'before the real TypeSafe AI client is adopted (see TDD §4.1, JEV.md).'
    )
