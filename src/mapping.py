'''`mapping` LLM task — one `Change` → the company items it affects.

Pipeline position: `map_change(change, obligations, projects, documents)`
is called once per Change by `pipeline.run_pipeline`. One LLM call per
change (v1 scale: ~5 changes per end-to-end run). Returns a fully-formed
`Mapping` with: `affected_items` (each carrying its own
`proposed_action` + `proposed_route`), `confidence`, and the free-text
`rationale`. There is NO mapping-level `recommended_action` or
`routing_destination` — per-item proposals replace the lossy summary
(TDD §8.6 closed in v1).

Index-based item selection (hallucination guard):
The LLM is asked for `AffectedItemByIndex(item_type, item_index, ...)` —
NOT the item id string. Candidate lists in the prompt are numbered
`[0]`, `[1]`, …, and the LLM picks indices. `_resolve_affected_item`
translates each index into the real `obligation_id` / `project_id` /
`document_id`. This design makes "the LLM invented an id that isn't in the
company context" structurally impossible — the index would be out of
range and raise. See PROMPT_CONVENTIONS.md "list-index selection".

Candidate-set size is a `DomainConfig.mapping.candidate_set_size`
hyperparameter — v1 fits the whole context in one call (~16 items). When
the company context exceeds ~200 items the mapping stage splits into the
three paths in TDD §3.6; the facade stays `map_change(change, obligations,
projects, documents)`.

Per-item routing: each AffectedItem carries its own
`proposed_action: Literal['review', 'amend', 'no_op']` and
`proposed_route: Literal['market_ops', 'legal', 'it_metering']`. One
Change touching N diverse assets produces N (action, route) proposals,
not one. Rachel's per-pair AssetDecision then approves / overrides /
dismisses / escalates each pair independently (TDD §3.7).

`stub_mapping_response` applies three naive filters (source_authority
substring match, obligation→project transitive hit, DER-in-title match)
and emits per-item action + route by item_type: obligations → amend /
market_ops; documents → review / legal; projects → review / market_ops.
Enough for StubBackend tests to exercise the index-resolution path
without an API call AND to produce multi-team per-pair outputs.
'''

from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.config import (
  AffectedItem,
  Change,
  Document,
  Mapping,
  Obligation,
  Project,
  load_domain_config,
)
from src.llm import CallSpec, PromptSpec
from src.model_caller import model_caller
from src.prompt_calibration import (
  MAPPING_CONFIDENCE_RUBRIC,
  RECOMMENDED_ACTION_RUBRIC,
  ROUTING_DESTINATION_GLOSSARY,
)

TASK_NAME = 'mapping'

MAPPING_JSON_EXAMPLE = """{
  "affected_items": [
    {
      "item_type": "<obligation|project|document>",
      "item_index": <int 0-based index into the matching candidate list>,
      "match_confidence": <int 0-5 per MAPPING_CONFIDENCE_RUBRIC>,
      "match_rationale": "<one sentence naming the specific overlap>",
      "proposed_action": "<review|amend|no_op>",
      "proposed_route": "<market_ops|legal|it_metering>"
    }
  ],
  "confidence": <int 0-5 per MAPPING_CONFIDENCE_RUBRIC>,
  "confidence_rationale": "<which rubric level you picked and why>",
  "rationale": "<one paragraph summarising WHY this change maps to these items>"
}"""

STUB_RATIONALE = (
  'stub mapping: assigns affected items by naive match on obligation '
  'source_authority / project name containing the proceeding_id; real impl '
  'calls the mapping LLM task'
)


class AffectedItemByIndex(BaseModel):
  model_config = ConfigDict(extra='forbid')
  item_type: Literal['obligation', 'project', 'document']
  item_index: int
  match_confidence: int
  match_rationale: str
  proposed_action: Literal['review', 'amend', 'no_op']
  proposed_route: Literal['market_ops', 'legal', 'it_metering']


class MappingResponse(BaseModel):
  model_config = ConfigDict(extra='forbid')
  affected_items: tuple[AffectedItemByIndex, ...]
  confidence: int
  confidence_rationale: str
  rationale: str


MAPPING_SPEC = CallSpec(
  purpose=(
    "For a given Change, identify which of Verdant's obligations, projects, "
    'and documents it affects; propose a per-item (action, route) for each '
    'affected item. One Change touching N diverse assets yields N '
    '(action, route) proposals — Rachel curates per-pair in the review '
    'queue (TDD §3.7).'
  ),
  acceptance_criteria=(
    'Mapping has at least one AffectedItem OR a rationale explaining why no '
    'obligation is affected; every emitted AffectedItem carries a '
    'proposed_action (review / amend / no_op) and a proposed_route '
    '(market_ops / legal / it_metering) that match the item\'s semantics.'
  ),
  failure_modes=(
    '(a) Over-mapping — flagging unrelated obligations as affected. '
    '(b) Per-item routing miscall — assigning a route that does not match '
    'what the item type ACTUALLY is (e.g., routing a tariff filing to '
    'market_ops instead of legal). '
    '(c) ID hallucination — picking an obligation_id not in the candidate list.'
  ),
)


def build_mapping_prompt(
  change: Change,
  obligations: list[Obligation],
  projects: list[Project],
  documents: list[Document],
  candidate_set_size: int,
) -> PromptSpec:
  change_summary = (
    f'change_type={change.change_type}\n'
    f'description={change.description}\n'
    f'confidence={change.confidence}'
  )
  obligation_list = '\n'.join(
    f'[{i}] {o.obligation_id} ({o.obligation_type}, {o.status}): {o.summary}'
    for i, o in enumerate(obligations[:candidate_set_size])
  )
  project_list = '\n'.join(
    f'[{i}] {p.project_id} ({p.status}): {p.name}'
    for i, p in enumerate(projects[:candidate_set_size])
  )
  document_list = '\n'.join(
    f'[{i}] {d.document_id} ({d.document_type}): {d.title}'
    for i, d in enumerate(documents[:candidate_set_size])
  )
  return PromptSpec(
    system_prompt=(
      'You are a regulatory-change impact mapper. You take a Change detected '
      'between proceeding versions and determine which of a specific '
      "company's obligations, projects, and documents it affects. You "
      'produce structured JSON output.'
    ),
    task=(
      'Map the Change below to its affected items. Recommend an action and '
      'route to the right internal team.'
    ),
    steps=(
      '1. Read the Change description. Identify what the change requires '
      'the regulated entity to START, STOP, or MODIFY.\n'
      '2. Scan each candidate list (obligations, projects, documents). For '
      'each candidate, ask: does the change substantively affect THIS '
      'item — not just topically mention it. "Topical overlap" is NOT '
      'the same as "affected".\n'
      '3. For each affected candidate, emit an AffectedItemByIndex with '
      'item_type, item_index, match_confidence, match_rationale, AND a '
      'per-item (proposed_action, proposed_route) pair. Decide the '
      'per-item action per the action rubric and the per-item route per '
      'the destination glossary — route is a property of the item\'s '
      'OWNERSHIP (which team owns this specific asset), not of the '
      'Change as a whole.\n'
      '4. Different items in the same mapping SHOULD route to different '
      'teams when the item types differ: a tariff-filing document '
      'routes to legal; a market-eligibility obligation routes to '
      'market_ops; a telemetry data feed routes to it_metering. Do NOT '
      'collapse per-item routes into one "primary team".\n'
      '5. Self-assess overall mapping confidence per the rubric; name '
      'the level in confidence_rationale.'
    ),
    details=(
      'Example 1 — narrow mapping (one obligation + one document):\n'
      '  Change (hypothetical SEC rule): "SEC amended Form X-17A-5 to '
      'require an additional capital-adequacy ratio in Schedule I."\n'
      '  Candidate obligations: [0] File Form X-17A-5 annually; [1] '
      'Maintain books and records; [2] Report OATS trade data.\n'
      '  Candidate documents: [0] Form X-17A-5 Template v2.1; [1] Books '
      'and Records Policy; [2] OATS Reporting Procedure.\n'
      '  Correct: TWO affected items — obligation [0] (filing duty '
      'now carries a new field) and document [0] (template needs the '
      'new column). match_confidence=5 on both. '
      'recommended_action="amend" (template needs a concrete update). '
      'routing_destination="legal" (external regulatory filing).\n'
      '\n'
      'Example 2 — empty mapping (no_op):\n'
      '  Change (hypothetical HHS rule): "HHS added a biennial '
      'minimum-necessary training requirement for all covered-entity '
      'workforce members."\n'
      '  Candidate obligations (utility tenant, no HIPAA relevance): '
      '[0] Register DER with RTO; [1] Maintain telemetry; [2] Monthly '
      'outage reporting.\n'
      '  Correct: ZERO affected items. '
      'recommended_action="no_op". The utility is not a HIPAA-covered '
      'entity and the training requirement does not touch anything in '
      'the candidate set. Still pick a routing_destination — route to '
      'the team closest to the change TOPIC (training = legal), with '
      'LOW confidence (1-2) and a confidence_rationale naming the '
      'no-op basis.\n'
      '\n'
      'Example 3 — fan-out mapping (multiple affected items):\n'
      '  Change (hypothetical FinCEN rule): "FinCEN tightened the CTR '
      'filing deadline from 15 to 10 calendar days and added a new '
      'wire-routing-path required field."\n'
      '  Candidate obligations: [0] File CTR for transactions over '
      '$10,000; [1] Monitor transaction activity continuously; [2] '
      'Train AML staff annually.\n'
      '  Candidate projects: [0] CTR Workflow Modernization; [1] AML '
      'Analyst Hiring FY26.\n'
      '  Candidate documents: [0] CTR Filing Procedure v4.2; [1] CTR '
      'Template; [2] AML Training Curriculum.\n'
      '  Correct: FOUR affected items — obligation [0], project [0], '
      'documents [0] and [1]. Obligation [1] (continuous monitoring) '
      'and obligation [2] (training) are topically adjacent but NOT '
      'substantively affected — the CTR deadline/field change does not '
      'touch monitoring or training. match_confidence=5 on the four '
      'affected items; match_confidence NOT emitted for the two '
      'topically-adjacent misses (they are omitted entirely). '
      'recommended_action="amend" (procedure + template need concrete '
      'updates). routing_destination="legal" (FinCEN filing).'
    ),
    field_intents=(
      '- affected_items[*].item_type: one of obligation / project / document.\n'
      '- affected_items[*].item_index: 0-based index into the matching '
      'candidate list in the Inputs section (obligation_candidates, '
      'project_candidates, or document_candidates per item_type). Count '
      'the "[0]" / "[1]" / ... prefixes. Must be a valid index into that '
      'list. Do NOT copy the item id string into this field — index-based '
      'selection prevents id hallucination (the harness resolves the '
      'index to the real id after).\n'
      '- affected_items[*].match_confidence: integer 0-5 per the confidence '
      'rubric below.\n'
      '- affected_items[*].match_rationale: one sentence naming the specific '
      'overlap between the change and this item.\n'
      '- affected_items[*].proposed_action: one of review / amend / no_op '
      'per the action rubric below — the action YOU recommend for THIS '
      'specific item (not for the Change as a whole).\n'
      '- affected_items[*].proposed_route: one of market_ops / legal / '
      'it_metering per the destination glossary below — the team that '
      'OWNS this specific item (based on item_type and the glossary\'s '
      '"owns" clauses). Different items in the SAME mapping can route '
      'to different teams.\n'
      '- confidence: integer 0-5 per the confidence rubric below (overall '
      'mapping confidence, not per-item).\n'
      '- confidence_rationale: one sentence naming the rubric level and why.\n'
      '- rationale: one paragraph summarising WHY this change maps to these '
      'items.\n'
      '- The harness owns mapping_id, tenant_id, environment, and change_id '
      '— do NOT emit these fields.'
    ),
    rubrics=(
      MAPPING_CONFIDENCE_RUBRIC
      + '\n\n'
      + RECOMMENDED_ACTION_RUBRIC
      + '\n\n'
      + ROUTING_DESTINATION_GLOSSARY
    ),
    pitfalls=(
      '- Do NOT map an item just because its name is topically adjacent '
      'to the change. "DER" appearing in both the change and the item '
      'title is a surface overlap, not a substantive effect. Ask: does '
      'the change require a concrete update to THIS item\'s compliance '
      'parameters / content / scope? If no, omit it. See the obligation '
      '[1] / [2] mismatches in Example 3.\n'
      '- Do NOT emit low-confidence match_confidence values to be safe. '
      'If match_confidence would be 2 or below, the item is not '
      'substantively affected — omit it entirely rather than including '
      'a hedge. Rachel\'s queue is noisier for every low-confidence '
      'match that ships; only emit matches you would defend.\n'
      '- Do NOT use HEDGE VERBS in match_rationale. If your rationale '
      'needs "may require", "should reflect", "likely impacts", "could '
      'affect", or any other hedge, the item is below the emission '
      'floor — omit it. A match worth emitting at match_confidence=3 '
      'states a SPECIFIC overlap in the form "the change requires X; '
      'the item specifies X" — not "the change may affect how X is '
      'handled." The hedge verb is the signal that you are reaching '
      'for topical adjacency rather than naming substantive effect.\n'
      '- Do NOT pick proposed_action="amend" on an item unless there is '
      'a known concrete edit implied for THAT item (new required field '
      'in this template, specific parameter edit on this obligation). '
      '"amend" means "we know what to change on this item"; "review" '
      'means "we need to look at this item first"; "no_op" means "this '
      'item is substantively affected but requires no edit." When '
      'uncertain between review and amend on a given item, prefer '
      'review.\n'
      '- Do NOT emit all affected items with the SAME proposed_route '
      'when the Change touches multiple teams\' domains. A tariff edit '
      'that also tightens a market-eligibility threshold produces at '
      'least two routes: legal for the tariff document, market_ops for '
      'the eligibility obligation. Routing by item ownership is what '
      'makes per-pair curation work in Rachel\'s queue — collapsing '
      'everything to one route defeats the point.'
    ),
    inputs_dict={
      'change_description': change_summary,
      'obligation_candidates': obligation_list,
      'project_candidates': project_list,
      'document_candidates': document_list,
    },
    json_example=MAPPING_JSON_EXAMPLE,
  )


_STUB_ACTION_BY_TYPE: dict[str, str] = {
  'obligation': 'amend',
  'document': 'review',
  'project': 'review',
}

_STUB_ROUTE_BY_TYPE: dict[str, str] = {
  'obligation': 'market_ops',
  'document': 'legal',
  'project': 'market_ops',
}


def stub_mapping_response(params: dict) -> MappingResponse:
  change: Change = params['change']
  obligations: list[Obligation] = params['obligations']
  projects: list[Project] = params['projects']
  documents: list[Document] = params['documents']
  by_index: list[AffectedItemByIndex] = []
  obligation_hits: set[str] = set()
  for i, obl in enumerate(obligations):
    if (
      change.proceeding_id in obl.source_authority.lower()
      or 'order 2222' in obl.source_authority.lower()
    ):
      by_index.append(
        AffectedItemByIndex(
          item_type='obligation',
          item_index=i,
          match_confidence=4,
          match_rationale=f'source_authority references {change.proceeding_id}',
          proposed_action=_STUB_ACTION_BY_TYPE['obligation'],  # type: ignore[arg-type]
          proposed_route=_STUB_ROUTE_BY_TYPE['obligation'],  # type: ignore[arg-type]
        )
      )
      obligation_hits.add(obl.obligation_id)
  for i, prj in enumerate(projects):
    if any(oid in prj.related_obligations for oid in obligation_hits):
      by_index.append(
        AffectedItemByIndex(
          item_type='project',
          item_index=i,
          match_confidence=3,
          match_rationale='project relates to an obligation affected by this change',
          proposed_action=_STUB_ACTION_BY_TYPE['project'],  # type: ignore[arg-type]
          proposed_route=_STUB_ROUTE_BY_TYPE['project'],  # type: ignore[arg-type]
        )
      )
  for i, doc in enumerate(documents):
    if 'der' in doc.title.lower() or 'der' in doc.summary.lower():
      by_index.append(
        AffectedItemByIndex(
          item_type='document',
          item_index=i,
          match_confidence=3,
          match_rationale='document title/summary references DER',
          proposed_action=_STUB_ACTION_BY_TYPE['document'],  # type: ignore[arg-type]
          proposed_route=_STUB_ROUTE_BY_TYPE['document'],  # type: ignore[arg-type]
        )
      )
  return MappingResponse(
    affected_items=tuple(by_index),
    confidence=min(4, max(2, change.confidence - 1)),
    confidence_rationale='stub: inherits change confidence with a one-step uncertainty penalty',
    rationale=STUB_RATIONALE,
  )


def map_change(
  change: Change,
  obligations: list[Obligation],
  projects: list[Project],
  documents: list[Document],
) -> Mapping:
  config = load_domain_config(change.tenant_id, change.environment)
  candidate_set_size = config.mapping.candidate_set_size
  obligations = obligations[:candidate_set_size]
  projects = projects[:candidate_set_size]
  documents = documents[:candidate_set_size]
  prompt = build_mapping_prompt(change, obligations, projects, documents, candidate_set_size)
  system, user = prompt.render()
  response = model_caller.call(
    task_name=TASK_NAME,
    params={
      'input': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
      'inputs_dict': prompt.inputs_dict,
      'change': change,
      'obligations': obligations,
      'projects': projects,
      'documents': documents,
    },
    response_type=MappingResponse,
    call_spec=MAPPING_SPEC,
    tenant_id=change.tenant_id,
    environment=change.environment,
  )
  affected_items = tuple(
    _resolve_affected_item(item, obligations, projects, documents)
    for item in response.affected_items
  )
  return Mapping(
    mapping_id=f'{change.change_id}:mapping',
    tenant_id=change.tenant_id,
    environment=change.environment,
    change_id=change.change_id,
    affected_items=affected_items,
    confidence=response.confidence,
    confidence_rationale=response.confidence_rationale,
    rationale=response.rationale,
  )


def _resolve_affected_item(
  item: AffectedItemByIndex,
  obligations: list[Obligation],
  projects: list[Project],
  documents: list[Document],
) -> AffectedItem:
  if item.item_type == 'obligation':
    target_id = obligations[item.item_index].obligation_id
  elif item.item_type == 'project':
    target_id = projects[item.item_index].project_id
  else:
    target_id = documents[item.item_index].document_id
  return AffectedItem(
    item_type=item.item_type,
    item_id=target_id,
    match_confidence=item.match_confidence,
    match_rationale=item.match_rationale,
    proposed_action=item.proposed_action,
    proposed_route=item.proposed_route,
  )
