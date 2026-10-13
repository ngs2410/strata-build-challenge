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
    sections.append(self._render_inputs(included_inputs))
    if self.json_example is not None:
      sections.append(f'## JSON Output Example\n{self.json_example}')
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
