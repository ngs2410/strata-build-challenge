'''Prompt-authoring primitives: `PromptSpec`, `CallSpec`, and `INPUT_LABELS`.

Every LLM task in Strata builds a `PromptSpec` and hands it to `ModelCaller`
alongside a `CallSpec`. This module defines both. See PROMPT_CONVENTIONS.md
for the full authoring guide — this docstring covers mechanics, not style.

`PromptSpec` (frozen dataclass) holds the full prompt as structured sections:
system_prompt, task (required), optional steps / details / rubrics /
pitfalls / field_intents / json_example, plus `inputs_dict` for the
run-specific inputs. `render(included_inputs)` returns a
`(system_prompt, user_message)` tuple. Section headings are hard-coded
(`## Task`, `## Steps`, …) so prompt structure is consistent across every
task.

Section order in the rendered user message is deliberate:
`## Task → ## Steps → ## Details → ## Rubrics → ## Pitfalls →
## Field Intents → ## JSON Output Example → ## Inputs`. The run-specific
`## Inputs` block sits at the END so the stable prefix (Task through
JSON Output Example) is byte-identical across every call of the same
task — this is what OpenAI's prompt-prefix caching hashes on. Keeping
Inputs last turns ~90% of each call's input tokens into cached-prefix
tokens on second+ runs within the cache window, cutting per-call cost.

`included_inputs` filters `inputs_dict` by key — set per
`TaskModelConfig.included_inputs` so each task controls which inputs it
actually injects. Keys not in `INPUT_LABELS` raise `KeyError` at render time
(no silent fallback) — register the label here when adding a new input.

`CallSpec` is the authoring intent: purpose, acceptance_criteria,
failure_modes. It is NOT a runtime config object; it is a review artefact
that every prompt author fills in so prompt diffs come with reviewer
context (CLAUDE.md: "every model call has a purpose, acceptance criteria,
and known failure modes").

Both specs are `frozen=True` — mutate by constructing a new instance.
'''

from dataclasses import dataclass, field

INPUT_LABELS: dict[str, str] = {
  'prior_version_text': 'Prior version text',
  'new_version_text': 'New version text',
  'passage_text': 'Passage text',
  'surrounding_context': 'Surrounding context',
  'claim': 'Claim',
  'cited_passage': 'Cited passage',
  'citing_span': 'Citing span',
  'change_description': 'Change description',
  'change_type': 'Change type',
  'change_citation': 'Change citation',
  'obligation_candidates': 'Candidate obligations',
  'project_candidates': 'Candidate projects',
  'document_candidates': 'Candidate documents',
}


@dataclass(frozen=True)
class PromptSpec:
  system_prompt: str
  task: str
  steps: str | None = None
  details: str | None = None
  rubrics: str | None = None
  pitfalls: str | None = None
  field_intents: str | None = None
  json_example: str | None = None
  inputs_dict: dict = field(default_factory=dict)

  def render(self, included_inputs: tuple[str, ...] = ()) -> tuple[str, str]:
    # Section order is deliberate for OpenAI's prompt-prefix caching:
    # everything from Task through JSON Output Example must be byte-identical
    # across every call of the same task so the stable prefix caches. Only
    # Inputs varies per call and sits at the end.
    sections: list[str] = [f'## Task\n{self.task}']
    for heading, body in (
      ('Steps', self.steps),
      ('Details', self.details),
      ('Rubrics', self.rubrics),
      ('Pitfalls', self.pitfalls),
      ('Field Intents', self.field_intents),
    ):
      if body is not None:
        sections.append(f'## {heading}\n{body}')
    if self.json_example is not None:
      sections.append(f'## JSON Output Example\n{self.json_example}')
    sections.append(self._render_inputs(included_inputs))
    user_message = '\n\n'.join(s for s in sections if s)
    return self.system_prompt, user_message

  def _render_inputs(self, included_inputs: tuple[str, ...]) -> str:
    if not self.inputs_dict:
      return ''
    if included_inputs:
      keys = [k for k in self.inputs_dict if k in included_inputs]
    else:
      keys = list(self.inputs_dict)
    if not keys:
      return ''
    lines = ['## Inputs']
    for key in keys:
      value = self.inputs_dict[key]
      if value == '' or value is None:
        continue
      label = INPUT_LABELS[key]
      lines.append(f'{label}: {value}')
    return '\n'.join(lines) if len(lines) > 1 else ''


@dataclass(frozen=True)
class CallSpec:
  purpose: str
  acceptance_criteria: str
  failure_modes: str
