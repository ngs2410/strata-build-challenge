from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.config import (
  AuditEvent,
  Change,
  Citation,
  DomainConfig,
  Obligation,
  Passage,
  ProceedingVersion,
  load_domain_config,
)


def _passage():
  return Passage(passage_id='p-1', section_path=('§1',), position=1, text='x')


def _citation():
  return Citation(
    passage_id='p-1',
    start_offset=0,
    end_offset=10,
    verified=False,
    verification_rationale='pending',
  )


def test_proceeding_version_round_trips_through_model_dump():
  v = ProceedingVersion(
    tenant_id='t',
    environment='e',
    proceeding_id='p',
    version_id='v',
    status='draft',
    docket_number='D1',
    issued_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    source_url='https://x',
    passages=(_passage(),),
  )
  reloaded = ProceedingVersion.model_validate(v.model_dump(mode='json'))
  assert reloaded.status == 'draft'
  assert reloaded.docket_number == 'D1'
  assert len(reloaded.passages) == 1
  assert reloaded.passages[0].passage_id == 'p-1'


def test_proceeding_version_rejects_status_outside_literal():
  with pytest.raises(ValidationError):
    ProceedingVersion(
      tenant_id='t',
      environment='e',
      proceeding_id='p',
      version_id='v',
      status='proposed',  # not in Literal['draft', 'final']
      docket_number='D1',
      issued_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
      source_url='https://x',
      passages=(_passage(),),
    )


def test_change_allows_null_prior_citation_for_added():
  c = Change(
    change_id='c-1',
    tenant_id='t',
    environment='e',
    proceeding_id='p',
    change_type='added',
    description='d',
    prior_citation=None,
    new_citation=_citation(),
    confidence=4,
    confidence_rationale='r',
    detected_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
  )
  assert c.prior_citation is None
  assert c.new_citation.passage_id == 'p-1'


def test_change_rejects_invalid_change_type():
  with pytest.raises(ValidationError):
    Change(
      change_id='c-1',
      tenant_id='t',
      environment='e',
      proceeding_id='p',
      change_type='rephrased',  # not in Literal
      description='d',
      prior_citation=None,
      new_citation=_citation(),
      confidence=4,
      confidence_rationale='r',
      detected_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )


def test_obligation_rejects_owner_outside_literal():
  with pytest.raises(ValidationError):
    Obligation(
      obligation_id='o',
      tenant_id='t',
      environment='e',
      obligation_type='reporting',
      summary='s',
      full_text='f',
      source_authority='a',
      status='active',
      owner='engineering',  # not in Literal
    )


def test_audit_event_rejects_invalid_kind():
  with pytest.raises(ValidationError):
    AuditEvent(
      event_id='e',
      tenant_id='t',
      environment='prod',
      occurred_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
      actor='system',
      kind='invented_kind',  # not in Literal
      target_type='change',
      target_id='x',
      prior_value_json=None,
      new_value_json=None,
      rationale=None,
    )


def test_load_domain_config_reads_default_file():
  config = load_domain_config('verdant_grid', 'prod')
  assert isinstance(config, DomainConfig)
  assert config.escalation.mapping_threshold == 3
  assert config.escalation.citation_threshold == 3
  assert config.mapping.candidate_set_size == 50
  assert config.mapping.context_window_tokens == 20000
  assert config.citation.verification_required is True
  assert config.citation.min_citation_span_chars == 10


def test_load_domain_config_deep_merges_tenant_override(tmp_path, monkeypatch):
  import orjson

  tenant_dir = tmp_path / 'overrides' / 'acme_corp' / 'staging'
  tenant_dir.mkdir(parents=True)
  (tenant_dir / 'domain_config.json').write_bytes(
    orjson.dumps({'escalation': {'mapping_threshold': 5}})
  )
  monkeypatch.setattr('src.config.TENANT_DATA_DIR', tmp_path / 'overrides')
  config = load_domain_config('acme_corp', 'staging')
  assert config.escalation.mapping_threshold == 5
  assert config.escalation.citation_threshold == 3  # unchanged from default
  assert 'version_diff' in config.task_model_configs  # task block unchanged


def test_load_domain_config_override_can_change_task_model_identifier(
  tmp_path, monkeypatch
):
  import orjson

  tenant_dir = tmp_path / 'overrides' / 'acme_corp' / 'prod'
  tenant_dir.mkdir(parents=True)
  (tenant_dir / 'domain_config.json').write_bytes(
    orjson.dumps(
      {'task_model_configs': {'mapping': {'model_identifier': 'openai://gpt-x'}}}
    )
  )
  monkeypatch.setattr('src.config.TENANT_DATA_DIR', tmp_path / 'overrides')
  config = load_domain_config('acme_corp', 'prod')
  assert config.task_model_configs['mapping'].model_identifier == 'openai://gpt-x'
  assert config.task_model_configs['mapping'].max_tokens == 8192  # unchanged
  # Non-overridden tasks are untouched
  assert config.task_model_configs['version_diff'].model_identifier == 'openai://gpt-5.4-nano'


def test_load_domain_config_ignores_missing_override_file(tmp_path, monkeypatch):
  monkeypatch.setattr('src.config.TENANT_DATA_DIR', tmp_path / 'empty')
  config = load_domain_config('nonexistent', 'prod')
  assert config.escalation.mapping_threshold == 3
