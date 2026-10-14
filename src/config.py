'''Canonical data contracts and tenant-scoped domain config loader.

Every entity in the pipeline — passages, proceeding versions, citations,
changes, extracted obligations, company-side obligations/projects/documents,
mappings, review items, audit events — is a Pydantic model defined here.
`DomainConfig` and its sub-models (`EscalationConfig`, `MappingConfig`,
`CitationConfig`, `TaskModelConfig`) are the per-tenant hyperparameter bundle.

Enforced invariants:
  * `extra='forbid'` on every model — unknown JSON keys raise, never silently
    pass through. Stale data on disk fails loudly (CLAUDE.md: rebuild the
    world, never add Pydantic defaults to accommodate old shapes).
  * Categorical fields use `Literal[...]` — no bare `str`. Draft vs final is
    `ProceedingVersion.status: Literal['draft', 'final']`, not a boolean
    (Strata product invariant).
  * `(tenant_id, environment)` lives on every tenant-scoped entity so the
    `Store[T]` facade can enforce isolation at read/write time.
  * Routing destination is single-valued today
    (`Literal['market_ops', 'legal', 'it_metering']`); multi-destination
    routing is deferred per TDD §8.6.

`load_domain_config(tenant_id, environment)` is the single reader. It
deep-merges `config/default_domain_config.json` with an optional per-tenant
override at `data/tenants/<tenant>/<env>/domain_config.json`, then
`DomainConfig.model_validate(merged)`. Callers that need config MUST route
through this function — never open the JSON directly (CLAUDE.md
encapsulation rule).
'''

from datetime import datetime
from pathlib import Path
from typing import Literal

import orjson
from pydantic import BaseModel, ConfigDict

CONFIG_DIR = Path('config')
DEFAULT_CONFIG_FILENAME = 'default_domain_config.json'
TENANT_DATA_DIR = Path('data/tenants')
TENANT_OVERRIDE_FILENAME = 'domain_config.json'


class Passage(BaseModel):
  model_config = ConfigDict(extra='forbid')
  passage_id: str
  section_path: tuple[str, ...]
  position: int
  text: str


class ProceedingVersion(BaseModel):
  model_config = ConfigDict(extra='forbid')
  tenant_id: str
  environment: str
  proceeding_id: str
  version_id: str
  status: Literal['draft', 'final']
  docket_number: str
  issued_at: datetime
  source_url: str
  passages: tuple[Passage, ...]


class Citation(BaseModel):
  model_config = ConfigDict(extra='forbid')
  passage_id: str
  start_offset: int
  end_offset: int
  verified: bool
  verification_rationale: str


class Change(BaseModel):
  model_config = ConfigDict(extra='forbid')
  change_id: str
  tenant_id: str
  environment: str
  proceeding_id: str
  change_type: Literal['added', 'removed', 'tightened', 'loosened', 'cosmetic']
  description: str
  prior_citation: Citation | None
  new_citation: Citation | None
  confidence: int
  confidence_rationale: str
  detected_at: datetime


class ExtractedObligation(BaseModel):
  model_config = ConfigDict(extra='forbid')
  extraction_id: str
  tenant_id: str
  environment: str
  proceeding_id: str
  version_id: str
  obligation_type: Literal['reporting', 'operational', 'procedural', 'financial']
  summary: str
  full_text: str
  citation: Citation
  confidence: int
  confidence_rationale: str


class Obligation(BaseModel):
  model_config = ConfigDict(extra='forbid')
  obligation_id: str
  tenant_id: str
  environment: str
  obligation_type: Literal['reporting', 'operational', 'procedural', 'financial']
  summary: str
  full_text: str
  source_authority: str
  status: Literal['active', 'proposed', 'retired']
  owner: Literal['market_ops', 'legal', 'it_metering']


class Project(BaseModel):
  model_config = ConfigDict(extra='forbid')
  project_id: str
  tenant_id: str
  environment: str
  name: str
  description: str
  status: Literal['planned', 'in_progress', 'blocked', 'complete']
  related_obligations: tuple[str, ...]
  owner: Literal['market_ops', 'legal', 'it_metering']


class Document(BaseModel):
  model_config = ConfigDict(extra='forbid')
  document_id: str
  tenant_id: str
  environment: str
  document_type: Literal['policy', 'procedure', 'tariff_filing', 'contract', 'template']
  title: str
  summary: str
  full_text: str
  last_reviewed_at: datetime
  owner: Literal['market_ops', 'legal', 'it_metering']


class AffectedItem(BaseModel):
  model_config = ConfigDict(extra='forbid')
  item_type: Literal['obligation', 'project', 'document']
  item_id: str
  match_confidence: int
  match_rationale: str
  # Per-item AI proposals. The mapping LLM task emits a (proposed_action,
  # proposed_route) pair per affected item — one Change can touch multiple
  # teams with different action urgency per asset (TDD §8.6 closed in v1).
  proposed_action: Literal['review', 'amend', 'no_op']
  proposed_route: Literal['market_ops', 'legal', 'it_metering']


class Mapping(BaseModel):
  model_config = ConfigDict(extra='forbid')
  mapping_id: str
  tenant_id: str
  environment: str
  change_id: str
  affected_items: tuple[AffectedItem, ...]
  # NO mapping-level recommended_action / routing_destination. Per-item
  # proposals live on each AffectedItem (TDD §8.6 closed in v1 — a lossy
  # mapping-level summary is strictly worse than per-item proposals).
  confidence: int
  confidence_rationale: str
  rationale: str


class ReviewItem(BaseModel):
  model_config = ConfigDict(extra='forbid')
  review_item_id: str
  tenant_id: str
  environment: str
  mapping_id: str
  priority: int
  # Status is a WRITE-TIME aggregate over the ReviewItem's AssetDecisions
  # (recomputed in review_queue.apply_asset_decisions). pending = at least
  # one affected_item still has no AssetDecision; escalated / overridden /
  # approved summarise the terminal set of per-pair decisions.
  status: Literal['pending', 'approved', 'overridden', 'escalated']
  created_at: datetime


class AssetDecision(BaseModel):
  model_config = ConfigDict(extra='forbid')
  decision_id: str
  tenant_id: str
  environment: str
  review_item_id: str
  mapping_id: str
  # Stable 0-based index into mapping.affected_items — the canonical key for
  # a (change × asset) pair. item_type and item_id are denormalised from the
  # resolved AffectedItem so the queue UI and audit payload don't need a
  # cross-store join on every read.
  affected_item_index: int
  item_type: Literal['obligation', 'project', 'document']
  item_id: str
  decision: Literal['approved', 'overridden', 'dismissed', 'escalated']
  # Rachel's chosen (action, route). Equal to the AffectedItem's proposed_*
  # when decision == 'approved'; may differ when decision == 'overridden'.
  action: Literal['review', 'amend', 'no_op']
  route: Literal['market_ops', 'legal', 'it_metering']
  rationale: str
  decided_at: datetime
  actor: str


class AuditEvent(BaseModel):
  model_config = ConfigDict(extra='forbid')
  event_id: str
  tenant_id: str
  environment: str
  occurred_at: datetime
  actor: str
  kind: Literal[
    'proceeding_ingested',
    'change_detected',
    'mapping_proposed',
    'review_item_created',
    'review_item_approved',
    'review_item_overridden',
    'review_item_escalated',
    'rollback_executed',
  ]
  target_type: Literal['proceeding_version', 'change', 'mapping', 'review_item']
  target_id: str
  prior_value_json: str | None
  new_value_json: str | None
  rationale: str | None


class EscalationConfig(BaseModel):
  model_config = ConfigDict(extra='forbid')
  mapping_threshold: int
  citation_threshold: int


class MappingConfig(BaseModel):
  model_config = ConfigDict(extra='forbid')
  candidate_set_size: int
  context_window_tokens: int


class CitationConfig(BaseModel):
  model_config = ConfigDict(extra='forbid')
  verification_required: bool
  min_citation_span_chars: int


class TaskModelConfig(BaseModel):
  model_config = ConfigDict(extra='forbid')
  model_identifier: str
  max_tokens: int
  reasoning_effort: Literal['none', 'low', 'medium', 'high']
  verbosity: Literal['low', 'medium', 'high']
  included_inputs: tuple[str, ...]


class DomainConfig(BaseModel):
  model_config = ConfigDict(extra='forbid')
  escalation: EscalationConfig
  mapping: MappingConfig
  citation: CitationConfig
  task_model_configs: dict[str, TaskModelConfig]


def load_domain_config(tenant_id: str, environment: str) -> DomainConfig:
  default_path = CONFIG_DIR / DEFAULT_CONFIG_FILENAME
  merged = orjson.loads(default_path.read_bytes())
  override_path = TENANT_DATA_DIR / tenant_id / environment / TENANT_OVERRIDE_FILENAME
  if override_path.is_file():
    override = orjson.loads(override_path.read_bytes())
    merged = _deep_merge(merged, override)
  return DomainConfig.model_validate(merged)


def _deep_merge(base: dict, override: dict) -> dict:
  result = dict(base)
  for key, override_value in override.items():
    if (
      key in result
      and isinstance(result[key], dict)
      and isinstance(override_value, dict)
    ):
      result[key] = _deep_merge(result[key], override_value)
    else:
      result[key] = override_value
  return result
