'''Task-name → prompt-builder + response-model + stub registry.

`PROMPT_REGISTRY` is the single lookup table that maps a task name (e.g.
`'version_diff'`) to everything `ModelCaller` needs to dispatch it: the
import path of the prompt builder, the Pydantic response class, a
deterministic stub (used in cache-less tests and `--dry_run` mode), and the
`task_model_configs` key where the model/token/effort hyperparameters live.

The registry is populated once at module load. `resolve(module_name,
function_name)` is a thin `importlib` + `getattr` helper so `ModelCaller`
doesn't need direct imports of every task module — this breaks what would
otherwise be a tangle of circular imports (every task imports its citation /
mapping neighbor transitively via shared Pydantic models).

Adding a new LLM task is a four-line insert here plus the three functions
the entry points at: `build_<task>_prompt`, `<Task>Response`,
`stub_<task>_response`. The CLAUDE.md "centralized flags" rule applies —
if a caller supplies a task name, the registry supplies everything else.

Current tasks: `version_diff`, `citation_verify`, `obligation_extraction`,
`mapping`.
'''

import importlib
from dataclasses import dataclass


@dataclass(frozen=True)
class PromptEntry:
  builder_module: str
  builder_function: str
  response_module: str
  response_class: str
  stub_module: str
  stub_function: str
  task_config_key: str


PROMPT_REGISTRY: dict[str, PromptEntry] = {
  'version_diff': PromptEntry(
    builder_module='src.version_diff',
    builder_function='build_version_diff_prompt',
    response_module='src.version_diff',
    response_class='VersionDiffResponse',
    stub_module='src.version_diff',
    stub_function='stub_version_diff_response',
    task_config_key='version_diff',
  ),
  'citation_verify': PromptEntry(
    builder_module='src.citation.citation_verify',
    builder_function='build_citation_verify_prompt',
    response_module='src.citation.citation_verify',
    response_class='CitationVerificationResponse',
    stub_module='src.citation.citation_verify',
    stub_function='stub_citation_verify_response',
    task_config_key='citation_verify',
  ),
  'obligation_extraction': PromptEntry(
    builder_module='src.obligation_extraction',
    builder_function='build_obligation_extraction_prompt',
    response_module='src.obligation_extraction',
    response_class='ObligationExtractionResponse',
    stub_module='src.obligation_extraction',
    stub_function='stub_obligation_extraction_response',
    task_config_key='obligation_extraction',
  ),
  'mapping': PromptEntry(
    builder_module='src.mapping',
    builder_function='build_mapping_prompt',
    response_module='src.mapping',
    response_class='MappingResponse',
    stub_module='src.mapping',
    stub_function='stub_mapping_response',
    task_config_key='mapping',
  ),
}


def resolve(module_name: str, function_name: str):
  module = importlib.import_module(module_name)
  return getattr(module, function_name)
