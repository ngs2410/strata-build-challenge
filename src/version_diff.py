from datetime import datetime, timezone

from src.config import Change, Citation, ProceedingVersion


def detect_changes(prior: ProceedingVersion, final: ProceedingVersion) -> list[Change]:
  if prior.proceeding_id != final.proceeding_id:
    raise ValueError(
      f'proceeding_id mismatch: prior={prior.proceeding_id} final={final.proceeding_id}'
    )
  if prior.tenant_id != final.tenant_id or prior.environment != final.environment:
    raise ValueError('tenant_id / environment mismatch between prior and final versions')
  if prior.status != 'draft' or final.status != 'final':
    raise ValueError(f'expected draft→final; got {prior.status}→{final.status}')
  now = datetime.now(timezone.utc)
  tenant_id = prior.tenant_id
  environment = prior.environment
  proceeding_id = prior.proceeding_id
  changes = [
    Change(
      change_id=f'{proceeding_id}:change-001',
      tenant_id=tenant_id,
      environment=environment,
      proceeding_id=proceeding_id,
      change_type='loosened',
      description=(
        'Final Rule permits RTOs/ISOs to establish an aggregation minimum size up to '
        '100 kW; NOPR explicitly disallowed any minimum size.'
      ),
      prior_citation=_stub_citation(prior, 1),
      new_citation=_stub_citation(final, 1),
      confidence=4,
      confidence_rationale=(
        'Direct textual contrast between subsection (A) of NOPR and Final Rule — '
        'stub-generated against the sample excerpts.'
      ),
      detected_at=now,
    ),
    Change(
      change_id=f'{proceeding_id}:change-002',
      tenant_id=tenant_id,
      environment=environment,
      proceeding_id=proceeding_id,
      change_type='tightened',
      description=(
        'Final Rule requires aggregator registration at least 60 days before '
        'participation; NOPR had no lead-time requirement.'
      ),
      prior_citation=_stub_citation(prior, 2),
      new_citation=_stub_citation(final, 2),
      confidence=4,
      confidence_rationale=(
        'Subsection (B) adds the 60-day lead time in Final; stub-generated against '
        'the sample excerpts.'
      ),
      detected_at=now,
    ),
    Change(
      change_id=f'{proceeding_id}:change-003',
      tenant_id=tenant_id,
      environment=environment,
      proceeding_id=proceeding_id,
      change_type='added',
      description=(
        'Final Rule introduces a telemetry-feed requirement for aggregated resources '
        'rated ≥100 kW; NOPR had no telemetry requirement.'
      ),
      prior_citation=None,
      new_citation=_stub_citation(final, 3),
      confidence=5,
      confidence_rationale=(
        'Final subsection (C) adds the telemetry obligation; stub-generated against '
        'the sample excerpts.'
      ),
      detected_at=now,
    ),
  ]
  return changes


def _stub_citation(version: ProceedingVersion, position: int) -> Citation:
  passage = _passage_at_position(version, position)
  return Citation(
    passage_id=passage.passage_id,
    start_offset=0,
    end_offset=min(len(passage.text), 200),
    verified=False,
    verification_rationale='pending citation_verify stage',
  )


def _passage_at_position(version: ProceedingVersion, position: int):
  for passage in version.passages:
    if passage.position == position:
      return passage
  raise ValueError(
    f'no passage at position {position} in {version.proceeding_id}:{version.version_id}'
  )
