from src.config import Citation, ExtractedObligation, ProceedingVersion

STUB_RATIONALE = (
  'stub extraction: hand-constructed example obligation covering the registration '
  'lead-time clause; real impl calls obligation_extraction LLM task'
)


def extract_obligations(version: ProceedingVersion) -> list[ExtractedObligation]:
  if not version.passages:
    return []
  anchor = version.passages[0]
  citation = Citation(
    passage_id=anchor.passage_id,
    start_offset=0,
    end_offset=min(len(anchor.text), 200),
    verified=True,
    verification_rationale='stub extraction attaches a pre-verified citation',
  )
  return [
    ExtractedObligation(
      extraction_id=f'{version.proceeding_id}:{version.version_id}:ext-001',
      tenant_id=version.tenant_id,
      environment=version.environment,
      proceeding_id=version.proceeding_id,
      version_id=version.version_id,
      obligation_type='procedural',
      summary=('Aggregators must register each DER with the RTO/ISO prior to its participation.'),
      full_text=anchor.text,
      citation=citation,
      confidence=4,
      confidence_rationale=STUB_RATIONALE,
    ),
  ]
