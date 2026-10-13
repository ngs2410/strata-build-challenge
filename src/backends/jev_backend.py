'''Jev (TypeSafe AI) backend placeholder for the `jev://` scheme.

v1 is intentionally a shell — `call()` raises `NotImplementedError`.
`matches()` is wired so a `jev://`-prefixed `model_identifier` dispatches
here rather than silently falling through to another backend; the raise
makes "did someone enable jev before the adapter landed?" visible.

A follow-up commit will replace the raise with Jev typed-decision dispatch
(Noul / Choice / Score — see JEV.md). Likely intermediate step: LLM-emulate
via OpenAI with Jev-shaped prompts so discrimination tasks
(`citation_verify`, `change_type_classify`, routing, action recommendation)
can run against the Jev seam before the real TypeSafe AI client is adopted.
Per TDD §4.1 the migration is a `pip install typesafe-sdk` + one import
flip; call sites stay backend-agnostic (they only know `jev://`).
'''

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
