from src.citation.llm_citation import LlmCitation
from src.config import (
  Change,
  Citation,
  ExtractedObligation,
  Passage,
  ProceedingVersion,
  load_domain_config,
)
from src.obligation_extraction import LlmExtractedObligation
from src.version_diff import LlmChange


def ground_citations_for_changes(
  llm_changes: list[LlmChange],
  prior: ProceedingVersion,
  final: ProceedingVersion,
) -> list[Change]:
  config = load_domain_config(prior.tenant_id, prior.environment)
  min_span = config.citation.min_citation_span_chars
  passages_by_id = _passages_by_id(prior, final)
  return [
    Change(
      change_id=lc.change_id,
      tenant_id=lc.tenant_id,
      environment=lc.environment,
      proceeding_id=lc.proceeding_id,
      change_type=lc.change_type,
      description=lc.description,
      prior_citation=_resolve(lc.prior_citation, passages_by_id, min_span),
      new_citation=_resolve(lc.new_citation, passages_by_id, min_span),
      confidence=lc.confidence,
      confidence_rationale=lc.confidence_rationale,
      detected_at=lc.detected_at,
    )
    for lc in llm_changes
  ]


def ground_obligations(
  llm_obligations: list[LlmExtractedObligation],
  version: ProceedingVersion,
) -> list[ExtractedObligation]:
  config = load_domain_config(version.tenant_id, version.environment)
  min_span = config.citation.min_citation_span_chars
  passages_by_id = {p.passage_id: p for p in version.passages}
  resolved: list[ExtractedObligation] = []
  for lo in llm_obligations:
    citation = _resolve(lo.citation, passages_by_id, min_span)
    if citation is None:
      raise ValueError(f'obligation {lo.extraction_id} has no citation')
    resolved.append(
      ExtractedObligation(
        extraction_id=lo.extraction_id,
        tenant_id=lo.tenant_id,
        environment=lo.environment,
        proceeding_id=lo.proceeding_id,
        version_id=lo.version_id,
        obligation_type=lo.obligation_type,
        summary=lo.summary,
        full_text=lo.full_text,
        citation=citation,
        confidence=lo.confidence,
        confidence_rationale=lo.confidence_rationale,
      )
    )
  return resolved


def _resolve(
  llm_citation: LlmCitation | None,
  passages_by_id: dict[str, Passage],
  min_span: int,
) -> Citation | None:
  if llm_citation is None:
    return None
  if llm_citation.passage_id not in passages_by_id:
    raise ValueError(f'citation references unknown passage_id: {llm_citation.passage_id}')
  passage = passages_by_id[llm_citation.passage_id]
  citing_span = llm_citation.citing_span
  if len(citing_span) < min_span:
    return Citation(
      passage_id=llm_citation.passage_id,
      start_offset=0,
      end_offset=0,
      verified=False,
      verification_rationale=(
        f'span width {len(citing_span)} below citation.min_citation_span_chars={min_span}'
      ),
    )
  start = passage.text.find(citing_span)
  if start < 0:
    return Citation(
      passage_id=llm_citation.passage_id,
      start_offset=0,
      end_offset=0,
      verified=False,
      verification_rationale='citing span not found in cited passage',
    )
  return Citation(
    passage_id=llm_citation.passage_id,
    start_offset=start,
    end_offset=start + len(citing_span),
    verified=False,
    verification_rationale='pending citation_verify stage',
  )


def _passages_by_id(
  prior: ProceedingVersion, final: ProceedingVersion
) -> dict[str, Passage]:
  return {p.passage_id: p for v in (prior, final) for p in v.passages}
