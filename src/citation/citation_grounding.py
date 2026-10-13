from src.config import Change, Citation, ProceedingVersion, load_domain_config


def ground_citations(
  changes: list[Change],
  prior: ProceedingVersion,
  final: ProceedingVersion,
) -> list[Change]:
  config = load_domain_config(prior.tenant_id, prior.environment)
  min_span = config.citation.min_citation_span_chars
  version_by_id = {prior.version_id: prior, final.version_id: final}
  passages_by_id = {
    passage.passage_id: (version, passage)
    for version in version_by_id.values()
    for passage in version.passages
  }
  grounded: list[Change] = []
  for change in changes:
    prior_c = _resolve(change.prior_citation, passages_by_id, min_span)
    new_c = _resolve(change.new_citation, passages_by_id, min_span)
    grounded.append(change.model_copy(update={'prior_citation': prior_c, 'new_citation': new_c}))
  return grounded


def _resolve(
  citation: Citation | None,
  passages_by_id: dict[str, object],
  min_span: int,
) -> Citation | None:
  if citation is None:
    return None
  entry = passages_by_id.get(citation.passage_id)
  if entry is None:
    raise ValueError(f'citation references unknown passage_id: {citation.passage_id}')
  _, passage = entry
  end = min(citation.end_offset, len(passage.text))
  span_width = end - citation.start_offset
  if span_width < min_span:
    return citation.model_copy(
      update={
        'end_offset': end,
        'verified': False,
        'verification_rationale': (
          f'span width {span_width} below citation.min_citation_span_chars={min_span}'
        ),
      }
    )
  return citation.model_copy(update={'end_offset': end})
