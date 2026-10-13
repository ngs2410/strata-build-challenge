'''Grounding step — deterministic substring match from `LlmCitation` to `Citation`.

Project depth area (two-step citation pattern): the LLM emits verbatim
`citing_span` text, this module turns it into precise `(start_offset,
end_offset)` by `passage.text.find(citing_span)`. No fuzzy matching, no
re-anchoring, no LLM round-trip. Counting characters is an LLM failure
mode so Python does it.

Two entry points:
  * `ground_citations_for_changes(llm_changes, prior, final)` — grounds
    both prior and new citations against the merged passage dict from BOTH
    versions (change citations can point into either). Returns fully-formed
    `Change` objects.
  * `ground_obligations(llm_obligations, version)` — grounds a single
    citation per obligation against the single version. Raises if an
    obligation has no citation (Strata product invariant: every claim is
    cited).

Failure cases (returned as `verified=False` with a specific
`verification_rationale`, not raised — these are legitimate "unverified,
needs reviewer" outcomes that `citation_verify` + the escalation threshold
handle downstream):
  * `citing_span` shorter than
    `DomainConfig.citation.min_citation_span_chars` — too thin to be a
    useful anchor; marked unverified with rationale.
  * `passage.text.find(citing_span)` returns -1 — the LLM paraphrased or
    truncated. Marked unverified with rationale.
  * Successful grounding sets `verified=False` with rationale
    `'pending citation_verify stage'` — the `citation_verify` LLM task
    flips this to `True`/`False` based on whether the cited span actually
    supports the stated change.

Hard failure (raises `ValueError`):
  * `passage_id` not found in any passed version — the LLM invented an id.
    This is a prompt-engineering bug, not a run-time data issue.
'''

from datetime import datetime, timezone

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

GROUNDING_PENDING_RATIONALE: str = 'pending citation_verify stage'
GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE: str = 'citing span not found in cited passage'
GROUNDING_FAILED_MIN_SPAN_FMT: str = (
  'span width {width} below citation.min_citation_span_chars={min_span}'
)


def is_grounding_pending(citation: Citation | None) -> bool:
  if citation is None:
    return False
  return citation.verification_rationale == GROUNDING_PENDING_RATIONALE


def ground_citations_for_changes(
  llm_changes: list[LlmChange],
  prior: ProceedingVersion,
  final: ProceedingVersion,
) -> list[Change]:
  config = load_domain_config(prior.tenant_id, prior.environment)
  min_span = config.citation.min_citation_span_chars
  now = datetime.now(timezone.utc)
  return [
    Change(
      change_id=f'{prior.proceeding_id}:change-{index + 1:03d}',
      tenant_id=prior.tenant_id,
      environment=prior.environment,
      proceeding_id=prior.proceeding_id,
      change_type=lc.change_type,
      description=lc.description,
      prior_citation=_resolve(lc.prior_citation, prior.passages, min_span),
      new_citation=_resolve(lc.new_citation, final.passages, min_span),
      confidence=lc.confidence,
      confidence_rationale=lc.confidence_rationale,
      detected_at=now,
    )
    for index, lc in enumerate(llm_changes)
  ]


def ground_obligations(
  llm_obligations: list[LlmExtractedObligation],
  version: ProceedingVersion,
) -> list[ExtractedObligation]:
  config = load_domain_config(version.tenant_id, version.environment)
  min_span = config.citation.min_citation_span_chars
  resolved: list[ExtractedObligation] = []
  for index, lo in enumerate(llm_obligations):
    citation = _resolve(lo.citation, version.passages, min_span)
    if citation is None:
      raise ValueError(f'extracted obligation at index {index} has no citation')
    resolved.append(
      ExtractedObligation(
        extraction_id=f'{version.proceeding_id}:{version.version_id}:ext-{index + 1:03d}',
        tenant_id=version.tenant_id,
        environment=version.environment,
        proceeding_id=version.proceeding_id,
        version_id=version.version_id,
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
  passages: tuple[Passage, ...],
  min_span: int,
) -> Citation | None:
  if llm_citation is None:
    return None
  if not (0 <= llm_citation.passage_index < len(passages)):
    raise ValueError(
      f'citation references passage_index={llm_citation.passage_index} '
      f'out of range [0, {len(passages)})'
    )
  passage = passages[llm_citation.passage_index]
  citing_span = llm_citation.citing_span
  if len(citing_span) < min_span:
    return Citation(
      passage_id=passage.passage_id,
      start_offset=0,
      end_offset=0,
      verified=False,
      verification_rationale=GROUNDING_FAILED_MIN_SPAN_FMT.format(
        width=len(citing_span), min_span=min_span
      ),
    )
  start = passage.text.find(citing_span)
  if start < 0:
    return Citation(
      passage_id=passage.passage_id,
      start_offset=0,
      end_offset=0,
      verified=False,
      verification_rationale=GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE,
    )
  return Citation(
    passage_id=passage.passage_id,
    start_offset=start,
    end_offset=start + len(citing_span),
    verified=False,
    verification_rationale=GROUNDING_PENDING_RATIONALE,
  )
