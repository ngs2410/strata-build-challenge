from datetime import datetime
from pathlib import Path
from typing import Literal

import orjson
from pydantic import BaseModel, ConfigDict

CONFIG_DIR = Path('config')
DEFAULT_CONFIG_FILENAME = 'default_domain_config.json'


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


class Mapping(BaseModel):
  model_config = ConfigDict(extra='forbid')
  mapping_id: str
  tenant_id: str
  environment: str
  change_id: str
  affected_items: tuple[AffectedItem, ...]
  recommended_action: Literal['review', 'amend', 'no_op']
  # Single destination is a v1 scope choice. Real changes often touch multiple
  # teams; expansion to tuple[Literal[...], ...] is open-question TDD §8.6.
  routing_destination: Literal['market_ops', 'legal', 'it_metering']
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
  status: Literal['pending', 'approved', 'overridden', 'escalated']
  created_at: datetime


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
  task_model_configs: dict[str, TaskModelConfig]


def load_domain_config(tenant_id: str, environment: str) -> DomainConfig:
  del tenant_id, environment
  default_path = CONFIG_DIR / DEFAULT_CONFIG_FILENAME
  raw = orjson.loads(default_path.read_bytes())
  return DomainConfig.model_validate(raw)
