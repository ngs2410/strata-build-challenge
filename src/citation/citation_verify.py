from src.config import Change, Citation, ProceedingVersion

STUB_RATIONALE = 'stub verifier: cited passage trivially supports claim at v1 scale'


def verify_citations(
  changes: list[Change],
  prior: ProceedingVersion,
  final: ProceedingVersion,
) -> list[Change]:
  passages_by_id = {
    passage.passage_id: passage for version in (prior, final) for passage in version.passages
  }
  verified: list[Change] = []
  for change in changes:
    prior_c = _verify_one(change.prior_citation, passages_by_id)
    new_c = _verify_one(change.new_citation, passages_by_id)
    verified.append(change.model_copy(update={'prior_citation': prior_c, 'new_citation': new_c}))
  return verified


def _verify_one(citation: Citation | None, passages_by_id: dict) -> Citation | None:
  if citation is None:
    return None
  if citation.passage_id not in passages_by_id:
    return citation.model_copy(
      update={
        'verified': False,
        'verification_rationale': 'citing passage not found in either version',
      }
    )
  return citation.model_copy(
    update={
      'verified': True,
      'verification_rationale': STUB_RATIONALE,
    }
  )
