from src.config import Change, Citation, ProceedingVersion


def ground_citations(
  changes: list[Change],
  prior: ProceedingVersion,
  final: ProceedingVersion,
) -> list[Change]:
  version_by_id = {prior.version_id: prior, final.version_id: final}
  passages_by_id = {
    passage.passage_id: (version, passage)
    for version in version_by_id.values()
    for passage in version.passages
  }
  grounded: list[Change] = []
  for change in changes:
    prior_c = _resolve(change.prior_citation, passages_by_id)
    new_c = _resolve(change.new_citation, passages_by_id)
    grounded.append(change.model_copy(update={'prior_citation': prior_c, 'new_citation': new_c}))
  return grounded


def _resolve(
  citation: Citation | None,
  passages_by_id: dict[str, object],
) -> Citation | None:
  if citation is None:
    return None
  entry = passages_by_id.get(citation.passage_id)
  if entry is None:
    raise ValueError(f'citation references unknown passage_id: {citation.passage_id}')
  _, passage = entry
  end = min(citation.end_offset, len(passage.text))
  return citation.model_copy(update={'end_offset': end})
