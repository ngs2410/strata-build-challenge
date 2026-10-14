from datetime import datetime, timezone

import pytest

from src.citation.citation_grounding import (
  GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE,
  GROUNDING_PENDING_RATIONALE,
  ground_citations_for_changes,
  ground_obligations,
)
from src.citation.citation_verify import (
  GROUNDING_FAILED_MARKER,
  build_citation_verify_prompt,
  verify_citations,
)
from src.citation.llm_citation import LlmCitation
from src.config import (
  AffectedItem,
  Change,
  Citation,
  CitationConfig,
  DomainConfig,
  EscalationConfig,
  Mapping,
  MappingConfig,
  Passage,
  ProceedingVersion,
)
from src.mapping import build_mapping_prompt, map_change
from src.obligation_extraction import build_obligation_extraction_prompt, extract_obligations
from src.pipeline import run_pipeline
from src.review_queue import enqueue_mapping
from src.version_diff import LlmChange, detect_changes


def _version(status, version_id):
  return ProceedingVersion(
    tenant_id='verdant_grid',
    environment='prod',
    proceeding_id='proc',
    version_id=version_id,
    status=status,
    docket_number='D1',
    issued_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    source_url='https://x',
    passages=(
      Passage(
        passage_id=f'proc:{version_id}:§1:p1',
        section_path=('§1',),
        position=1,
        text='The aggregator shall register each DER before participation.',
      ),
      Passage(
        passage_id=f'proc:{version_id}:§1:p2',
        section_path=('§1',),
        position=2,
        text='The aggregator shall provide a telemetry feed.',
      ),
      Passage(
        passage_id=f'proc:{version_id}:§1:p3',
        section_path=('§1',),
        position=3,
        text='The RTO may establish a minimum size requirement.',
      ),
    ),
  )


def _change(change_type, prior_citation=None, new_citation=None):
  return Change(
    change_id='c-1',
    tenant_id='verdant_grid',
    environment='prod',
    proceeding_id='proc',
    change_type=change_type,
    description='d',
    prior_citation=prior_citation,
    new_citation=new_citation,
    confidence=4,
    confidence_rationale='r',
    detected_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
  )


def _llm_change(change_type, prior_citation=None, new_citation=None):
  return LlmChange(
    change_type=change_type,
    description='d',
    prior_citation=prior_citation,
    new_citation=new_citation,
    confidence=4,
    confidence_rationale='r',
  )


def _mapping(confidence, affected_items=(), change_id='c-1'):
  return Mapping(
    mapping_id='m',
    tenant_id='t',
    environment='e',
    change_id=change_id,
    affected_items=tuple(affected_items),
    confidence=confidence,
    confidence_rationale='r',
    rationale='r',
  )


def _verified_citation() -> Citation:
  return Citation(
    passage_id='proc:v:§1:p1',
    start_offset=0,
    end_offset=10,
    verified=True,
    verification_rationale='verified in test',
  )


def _verified_change(change_type='tightened'):
  return _change(
    change_type,
    prior_citation=_verified_citation(),
    new_citation=_verified_citation(),
  )


def _config(threshold):
  return DomainConfig(
    escalation=EscalationConfig(mapping_threshold=threshold, citation_threshold=3),
    mapping=MappingConfig(candidate_set_size=50, context_window_tokens=20000),
    citation=CitationConfig(verification_required=True, min_citation_span_chars=10),
    task_model_configs={},
  )


def test_detect_changes_returns_three_llm_changes_in_known_order():
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  llm_changes = detect_changes(nopr, final)
  assert [c.change_type for c in llm_changes] == ['loosened', 'tightened', 'added']
  assert llm_changes[2].prior_citation is None  # 'added' change has no prior
  assert llm_changes[0].prior_citation is not None
  assert llm_changes[0].prior_citation.passage_index == 0
  # LlmCitation carries citing_span (verbatim text), NOT offsets
  assert llm_changes[0].prior_citation.citing_span
  assert not hasattr(llm_changes[0].prior_citation, 'start_offset')


def test_detect_changes_rejects_mismatched_statuses():
  with pytest.raises(ValueError, match='draft'):
    detect_changes(_version('draft', 'a'), _version('draft', 'b'))


def test_version_diff_prompt_examples_are_from_adjacent_domains_not_order_2222():
  # Worked examples teach a pattern — they must not restate the Order 2222
  # test input or the model is "learning" by matching rather than by rule.
  # The examples are drawn from SEC (broker-dealer), HHS (HIPAA), and
  # FinCEN (SAR filing) per PRD §5's adjacent-domain expansion list.
  from src.config import ProceedingVersion
  from src.version_diff import build_version_diff_prompt

  def _blank(status: str, version_id: str) -> ProceedingVersion:
    return ProceedingVersion(
      tenant_id='t',
      environment='e',
      proceeding_id='p',
      version_id=version_id,
      status=status,  # type: ignore[arg-type]
      docket_number='D',
      issued_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
      source_url='https://x',
      passages=(),
    )

  spec = build_version_diff_prompt(_blank('draft', 'a'), _blank('final', 'b'))
  _, user = spec.render()
  details_section = user.split('## Details')[1].split('## Rubrics')[0]
  # Order 2222 fact patterns MUST NOT appear in Details — those are what we
  # test against. If they do, the model is being "taught to the test".
  assert 'DER' not in details_section
  assert 'RTO' not in details_section  # avoids RTO/ISO
  assert 'telemetry' not in details_section.lower()
  assert '100 kW' not in details_section
  assert '60 days' not in details_section
  # Positive — all three adjacent-domain examples should appear.
  assert 'broker-dealer' in details_section  # Example 1 (SEC)
  assert 'HIPAA' in details_section  # Example 2 (HHS)
  assert 'FinCEN' in details_section  # Example 3 (banking / OCC)


def test_ground_citations_finds_substring_and_computes_offsets():
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  known_span = 'The aggregator shall register'
  llm_cit = LlmCitation(passage_index=0, citing_span=known_span)
  lc = _llm_change('tightened', prior_citation=llm_cit)
  grounded = ground_citations_for_changes([lc], nopr, final)
  assert grounded[0].prior_citation.passage_id == 'proc:nopr:§1:p1'
  assert grounded[0].prior_citation.start_offset == 0
  assert grounded[0].prior_citation.end_offset == len(known_span)
  assert grounded[0].prior_citation.verified is False  # pending verify stage
  assert grounded[0].prior_citation.verification_rationale == GROUNDING_PENDING_RATIONALE


def test_ground_citations_marks_unverified_when_citing_span_not_found():
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  absent = LlmCitation(
    passage_index=0,
    citing_span='text that is definitely not in the passage',
  )
  lc = _llm_change('tightened', prior_citation=absent)
  grounded = ground_citations_for_changes([lc], nopr, final)
  assert grounded[0].prior_citation.verified is False
  assert 'not found' in grounded[0].prior_citation.verification_rationale


def test_ground_citations_raises_on_out_of_range_passage_index():
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  bogus = LlmCitation(passage_index=99, citing_span='anything')
  lc = _llm_change('tightened', prior_citation=bogus)
  with pytest.raises(ValueError, match='out of range'):
    ground_citations_for_changes([lc], nopr, final)


def _version_with_passage_text(text: str, version_id: str = 'nopr') -> ProceedingVersion:
  return ProceedingVersion(
    tenant_id='verdant_grid',
    environment='prod',
    proceeding_id='proc',
    version_id=version_id,
    status='draft',
    docket_number='D1',
    issued_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    source_url='https://x',
    passages=(
      Passage(
        passage_id=f'proc:{version_id}:§1:p1',
        section_path=('§1',),
        position=1,
        text=text,
      ),
    ),
  )


def test_ground_citations_whitespace_normalized_fallback_matches_newline_in_passage():
  # The LLM collapses newlines to spaces inside citing_span routinely at
  # gpt-5.4-nano. Exact find() misses, but after whitespace normalization
  # both sides collapse to the same string. The stored Citation's offsets
  # MUST slice back to the ORIGINAL passage text (preserving the newline),
  # not the normalized form.
  original_text = (
    'The aggregator shall not include a minimum size\n'
    'requirement for the aggregated resources.'
  )
  nopr = _version_with_passage_text(original_text)
  final = _version_with_passage_text(original_text, version_id='final')
  # LLM collapsed the \n to a space.
  collapsed_span = 'shall not include a minimum size requirement for the aggregated resources'
  llm_cit = LlmCitation(passage_index=0, citing_span=collapsed_span)
  lc = _llm_change('tightened', prior_citation=llm_cit)
  grounded = ground_citations_for_changes([lc], nopr, final)
  citation = grounded[0].prior_citation
  assert citation.verification_rationale == GROUNDING_PENDING_RATIONALE
  # Offsets slice back to the ORIGINAL text — preserving the newline.
  original_substring = original_text[citation.start_offset : citation.end_offset]
  assert original_substring == (
    'shall not include a minimum size\nrequirement for the aggregated resources'
  )


def test_ground_citations_whitespace_normalized_fallback_handles_multiple_runs():
  # Multiple whitespace runs (double spaces, newlines, tabs) in the
  # passage all collapse to single spaces in the normalized form. The
  # back-mapped offsets must span from the first non-space char to the
  # last — covering the full original substring including the embedded
  # whitespace runs.
  original_text = 'foo  \n  bar\t\tbaz'  # "foo", then whitespace run, "bar", tab run, "baz"
  nopr = _version_with_passage_text(original_text)
  final = _version_with_passage_text(original_text, version_id='final')
  collapsed_span = 'foo bar baz'
  llm_cit = LlmCitation(passage_index=0, citing_span=collapsed_span)
  lc = _llm_change('tightened', prior_citation=llm_cit)
  grounded = ground_citations_for_changes([lc], nopr, final)
  citation = grounded[0].prior_citation
  assert citation.verification_rationale == GROUNDING_PENDING_RATIONALE
  assert original_text[citation.start_offset : citation.end_offset] == 'foo  \n  bar\t\tbaz'


def test_ground_citations_exact_match_preferred_over_normalized_fallback():
  # When the LLM gives us the span verbatim with its original whitespace,
  # the exact fast path should fire and we should get the exact offsets
  # (no normalization artifact).
  original_text = (
    'The aggregator shall not include a minimum size\n'
    'requirement for the aggregated resources.'
  )
  nopr = _version_with_passage_text(original_text)
  final = _version_with_passage_text(original_text, version_id='final')
  verbatim_span = 'shall not include a minimum size\nrequirement for the aggregated resources'
  llm_cit = LlmCitation(passage_index=0, citing_span=verbatim_span)
  lc = _llm_change('tightened', prior_citation=llm_cit)
  grounded = ground_citations_for_changes([lc], nopr, final)
  citation = grounded[0].prior_citation
  assert citation.verification_rationale == GROUNDING_PENDING_RATIONALE
  assert citation.start_offset == original_text.find(verbatim_span)
  assert citation.end_offset == citation.start_offset + len(verbatim_span)


def test_ground_citations_normalized_fallback_still_fails_on_real_mismatch():
  # Whitespace normalization must not cause false positives — if the
  # LLM paraphrased content words (not just whitespace), the match
  # should still fail.
  original_text = 'The aggregator shall register.'
  nopr = _version_with_passage_text(original_text)
  final = _version_with_passage_text(original_text, version_id='final')
  paraphrase = 'The aggregator must register'  # "shall" → "must"
  llm_cit = LlmCitation(passage_index=0, citing_span=paraphrase)
  lc = _llm_change('tightened', prior_citation=llm_cit)
  grounded = ground_citations_for_changes([lc], nopr, final)
  assert grounded[0].prior_citation.verification_rationale == (
    GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE
  )


def test_ground_citations_min_span_check_uses_normalized_width():
  # The min-span floor applies to the semantic anchor width (post-
  # whitespace-collapse). A span of "The    " (3 non-whitespace chars
  # padded with whitespace) must trip the min_span sentinel.
  original_text = 'The aggregator shall register.'
  nopr = _version_with_passage_text(original_text)
  final = _version_with_passage_text(original_text, version_id='final')
  padded = LlmCitation(passage_index=0, citing_span='The      ')  # 3 real chars
  lc = _llm_change('tightened', prior_citation=padded)
  grounded = ground_citations_for_changes([lc], nopr, final)
  assert 'min_citation_span_chars' in grounded[0].prior_citation.verification_rationale


def test_ground_citations_marks_unverified_when_span_below_min(verdant_grid_seeded):
  del verdant_grid_seeded
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  # min_citation_span_chars defaults to 10 in the Verdant config. A 3-char
  # citing_span is below that floor and must flip verified=False with a
  # specific rationale, so citation_threshold at review time picks it up.
  narrow = LlmCitation(passage_index=0, citing_span='The')
  lc = _llm_change('tightened', prior_citation=narrow)
  grounded = ground_citations_for_changes([lc], nopr, final)
  assert grounded[0].prior_citation.verified is False
  assert 'min_citation_span_chars' in grounded[0].prior_citation.verification_rationale


def test_verify_citations_sets_verified_true_for_known_passage():
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  citation = Citation(
    passage_id='proc:nopr:§1:p1',
    start_offset=0,
    end_offset=10,
    verified=False,
    verification_rationale=GROUNDING_PENDING_RATIONALE,
  )
  verified = verify_citations([_change('tightened', prior_citation=citation)], nopr, final)
  assert verified[0].prior_citation.verified is True


def test_verify_citations_preserves_grounding_failure_without_calling_llm():
  # When grounding has already stamped a citation unverified (span not
  # found), the LLM verifier must NOT be allowed to vote "true" on the
  # structurally-broken citation. The grounding verdict is preserved
  # verbatim.
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  failed = Citation(
    passage_id='proc:nopr:§1:p1',
    start_offset=0,
    end_offset=0,
    verified=False,
    verification_rationale=GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE,
  )
  verified = verify_citations([_change('tightened', prior_citation=failed)], nopr, final)
  assert verified[0].prior_citation.verified is False
  assert verified[0].prior_citation.verification_rationale == (
    GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE
  )


def test_verify_citations_short_circuits_when_no_change_has_pending_citation():
  # Every citation already failed grounding — the LLM must not be
  # called. We assert this by passing a change list that would otherwise
  # trigger the stub verifier (which marks pending citations verified)
  # and confirming the failed sentinel survives.
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  prior_failed = Citation(
    passage_id='proc:nopr:§1:p1',
    start_offset=0,
    end_offset=0,
    verified=False,
    verification_rationale=GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE,
  )
  new_failed = Citation(
    passage_id='proc:final:§1:p1',
    start_offset=0,
    end_offset=0,
    verified=False,
    verification_rationale=GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE,
  )
  change = _change('tightened', prior_citation=prior_failed, new_citation=new_failed)
  verified = verify_citations([change], nopr, final)
  assert verified[0].prior_citation.verified is False
  assert verified[0].new_citation.verified is False
  assert verified[0].prior_citation.verification_rationale == (
    GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE
  )
  assert verified[0].new_citation.verification_rationale == (
    GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE
  )


def test_citation_verify_prompt_has_steps_details_pitfalls_sections():
  # Match the version_diff prompt scaffolding — the verifier needs a
  # step-by-step decision procedure, worked examples drawn from adjacent
  # domains, and explicit pitfalls. The three failure modes we're
  # teaching against are (a) accepting a topically-adjacent span that
  # doesn't actually support the claim, (b) rejecting a correct parent-
  # obligation span on a tightened change's prior_citation, (c)
  # paraphrasing the description instead of quoting the passage.
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  citation = Citation(
    passage_id='proc:nopr:§1:p1',
    start_offset=0,
    end_offset=29,
    verified=False,
    verification_rationale=GROUNDING_PENDING_RATIONALE,
  )
  prompt = build_citation_verify_prompt(
    [_change('tightened', prior_citation=citation)], nopr, final
  )
  _, user = prompt.render()
  assert '## Steps' in user
  assert '## Details' in user
  assert '## Pitfalls' in user
  # Worked examples are drawn from adjacent regulatory domains, NOT the
  # Order 2222 input itself — same rule as version_diff.
  details_section = user.split('## Details')[1].split('## Rubrics')[0]
  assert 'minimum-necessary' in details_section  # HIPAA Example 1
  assert 'SAR' in details_section  # FinCEN Example 3
  assert 'DER' not in details_section  # Order 2222 would be teaching-to-test
  # Pitfalls name the three failure modes.
  pitfalls_section = user.split('## Pitfalls')[1].split('## Field Intents')[0]
  assert 'topically relevant' in pitfalls_section
  assert 'paraphrase' in pitfalls_section.lower()


def test_citation_verify_prompt_renders_failed_grounding_sides_as_marker():
  # A grounding-failed side wastes tokens and distracts the model when
  # rendered as "passage text: ... / citing span: """ (empty). It should
  # render as a terse GROUNDING_FAILED marker so the LLM focuses on the
  # pending sides. Correctness is still guaranteed by the merge-step
  # preservation; this is a distraction-reduction nicety.
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  pending = Citation(
    passage_id='proc:nopr:§1:p1',
    start_offset=0,
    end_offset=29,
    verified=False,
    verification_rationale=GROUNDING_PENDING_RATIONALE,
  )
  failed = Citation(
    passage_id='proc:final:§1:p1',
    start_offset=0,
    end_offset=0,
    verified=False,
    verification_rationale=GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE,
  )
  prompt = build_citation_verify_prompt(
    [_change('tightened', prior_citation=pending, new_citation=failed)], nopr, final
  )
  _, user = prompt.render()
  # Pending side: full passage + span rendered.
  assert 'The aggregator shall register' in user
  # Failed side: terse marker, no passage text, no empty citing span.
  assert f'new_citation: {GROUNDING_FAILED_MARKER}' in user
  # The failed passage text must NOT appear a second time under new_citation.
  new_citation_section = user.split('new_citation:')[1].split('[')[0]
  assert 'passage text' not in new_citation_section
  assert 'citing span' not in new_citation_section


def test_citation_verify_prompt_pitfalls_name_the_grounding_failed_marker():
  # The LLM needs to know what to do when it sees the marker: emit
  # verified=null + rationale=null, don't try to verify from description.
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  citation = Citation(
    passage_id='proc:nopr:§1:p1',
    start_offset=0,
    end_offset=29,
    verified=False,
    verification_rationale=GROUNDING_PENDING_RATIONALE,
  )
  prompt = build_citation_verify_prompt(
    [_change('tightened', prior_citation=citation)], nopr, final
  )
  _, user = prompt.render()
  assert GROUNDING_FAILED_MARKER in user.split('## Pitfalls')[1].split('## Field Intents')[0]
  assert GROUNDING_FAILED_MARKER in user.split('## Steps')[1].split('## Details')[0]


def test_citation_verify_prompt_does_not_leak_structural_metadata():
  # The verifier only needs passage text + citing span + the described
  # shift. Rendering passage_id strings or integer offsets is noise that
  # the LLM can only misread (and we moved away from exposing passage_id
  # to the version_diff LLM for the same reason).
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  citation = Citation(
    passage_id='proc:nopr:§1:p1',
    start_offset=0,
    end_offset=29,
    verified=False,
    verification_rationale=GROUNDING_PENDING_RATIONALE,
  )
  prompt = build_citation_verify_prompt(
    [_change('tightened', prior_citation=citation)], nopr, final
  )
  _, user = prompt.render()
  assert 'proc:nopr:§1:p1' not in user
  assert 'passage_id=' not in user
  assert 'offsets ' not in user
  assert 'passage text:' in user
  assert 'citing span:' in user


def test_verify_citations_mixed_pending_and_failed_sides_preserves_failed_side():
  # A change with one pending citation and one grounding-failed citation.
  # The LLM is called, but its vote only applies to the pending side —
  # the failed side keeps its grounding rationale verbatim.
  nopr = _version('draft', 'nopr')
  final = _version('final', 'final')
  pending = Citation(
    passage_id='proc:nopr:§1:p1',
    start_offset=0,
    end_offset=10,
    verified=False,
    verification_rationale=GROUNDING_PENDING_RATIONALE,
  )
  failed = Citation(
    passage_id='proc:final:§1:p1',
    start_offset=0,
    end_offset=0,
    verified=False,
    verification_rationale=GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE,
  )
  change = _change('tightened', prior_citation=pending, new_citation=failed)
  verified = verify_citations([change], nopr, final)
  assert verified[0].prior_citation.verified is True  # LLM voted on pending
  assert verified[0].new_citation.verified is False  # grounding failure preserved
  assert verified[0].new_citation.verification_rationale == (
    GROUNDING_FAILED_SPAN_NOT_FOUND_RATIONALE
  )


def test_obligation_extraction_prompt_teaches_content_specification_merge():
  # Observed over-split at gpt-5.4-nano on §(B): the LLM extracted
  # "The registration shall include the physical location..." as a
  # separate obligation from "The aggregator shall register each DER".
  # The content-specification sentence has no independent compliance
  # surface — violating it is violating the SAME registration duty.
  # Example 6 (hypothetical FinCEN CTR filing with content spec)
  # teaches the merge; the matching Pitfall names the "The X shall
  # include Y, Z" pattern.
  version = _version('draft', 'nopr')
  prompt = build_obligation_extraction_prompt(version)
  _, user = prompt.render()
  details_section = user.split('## Details')[1].split('## Rubrics')[0]
  assert 'Example 6' in details_section
  assert 'currency transaction report' in details_section
  assert 'content specification' in details_section.lower() or 'CONTENT' in details_section
  pitfalls_section = user.split('## Pitfalls')[1].split('## Field Intents')[0]
  assert 'shall include' in pitfalls_section
  assert 'independent compliance surface' in pitfalls_section


def test_obligation_extraction_prompt_teaches_process_as_subject_filter():
  # Observed failure mode at gpt-5.4-nano: §(A)'s "The aggregator
  # registration process shall not include a minimum size requirement"
  # was extracted as an aggregator obligation because the LLM latched
  # onto the surface mention of "aggregator" in the subject phrase. The
  # process is actually administered by the RTO/ISO, so the sentence is
  # a regulator-side cap on the RTO's rulemaking authority. Example 5
  # teaches the "who administers the process" decision test.
  version = _version('draft', 'nopr')
  prompt = build_obligation_extraction_prompt(version)
  _, user = prompt.render()
  details_section = user.split('## Details')[1].split('## Rubrics')[0]
  assert 'Example 5' in details_section
  assert 'broker-dealer registration process' in details_section
  assert 'ADMINISTERS' in details_section or 'administers' in details_section.lower()
  pitfalls_section = user.split('## Pitfalls')[1].split('## Field Intents')[0]
  assert 'SUBJECT is a PROCESS' in pitfalls_section
  assert 'the aggregator registration process' in pitfalls_section


def test_obligation_extraction_rubrics_contains_only_taxonomy_and_confidence():
  # Rubrics is just OBLIGATION_TAXONOMY_REFERENCE + CONFIDENCE_LEVEL_RUBRIC.
  # The old "Additional rules" subsection was duplicated across Steps and
  # Field Intents, so it was removed in favour of a single source. The
  # critical "no numeric offsets" rule is preserved in Field Intents.
  version = _version('draft', 'nopr')
  prompt = build_obligation_extraction_prompt(version)
  _, user = prompt.render()
  rubrics_section = user.split('## Rubrics')[1].split('## Pitfalls')[0]
  assert 'Additional rules' not in rubrics_section
  # Field Intents preserves the no-numeric-offsets correctness rule.
  field_intents_section = user.split('## Field Intents')[1].split('## JSON Output Example')[0]
  assert 'numeric offsets' in field_intents_section
  assert 'grounding is done deterministically by Python' in field_intents_section


def test_obligation_extraction_prompt_names_regulator_side_filter():
  # The perspective rule is baked into OBLIGATION_TAXONOMY_REFERENCE
  # (via prompt_calibration) AND reinforced by a concrete Example 4 +
  # the first Pitfall. Without this guard the extractor would treat
  # "the RTO shall accept bids" as an obligation of the aggregator,
  # polluting the aggregator's compliance set with regulator duties.
  version = _version('draft', 'nopr')
  prompt = build_obligation_extraction_prompt(version)
  _, user = prompt.render()
  # Perspective rule is present in Rubrics (via OBLIGATION_TAXONOMY_REFERENCE).
  rubrics_section = user.split('## Rubrics')[1].split('## Pitfalls')[0]
  assert 'REGULATED ENTITY' in rubrics_section
  # Example 4 (FinCEN regulator-side filter) is in Details.
  details_section = user.split('## Details')[1].split('## Rubrics')[0]
  assert 'FinCEN' in details_section
  assert 'REGULATOR-side' in details_section or 'regulator-side' in details_section.lower()
  # Pitfalls lead with the regulator-side filter.
  pitfalls_section = user.split('## Pitfalls')[1].split('## Field Intents')[0]
  assert 'REGULATOR-side' in pitfalls_section or 'regulator-side' in pitfalls_section.lower()
  assert 'The Commission shall' in pitfalls_section
  assert 'The RTO' in pitfalls_section


def test_obligation_extraction_prompt_has_steps_details_pitfalls_sections():
  # Parity with version_diff and citation_verify. Scaffolding gives the
  # LLM an explicit decision procedure, three worked examples from
  # adjacent domains, and failure-mode guards. The three teaching cases
  # are (a) one duty → one obligation (SEC Form X-17A-5), (b) one
  # sentence → two obligations (HIPAA compound), (c) shortest-span
  # discipline (SEC with modifier).
  version = _version('draft', 'nopr')
  prompt = build_obligation_extraction_prompt(version)
  _, user = prompt.render()
  assert '## Steps' in user
  assert '## Details' in user
  assert '## Pitfalls' in user
  details_section = user.split('## Details')[1].split('## Rubrics')[0]
  # Adjacent-domain examples — not Order 2222 (don't teach to test).
  assert 'X-17A-5' in details_section  # SEC Example 1 and 3
  assert 'HIPAA' in details_section or 'minimum necessary' in details_section  # HHS Example 2
  assert 'DER' not in details_section  # Order 2222 teaching-to-test guard
  # Pitfalls name the four failure modes we're guarding against.
  pitfalls_section = user.split('## Pitfalls')[1].split('## Field Intents')[0]
  assert 'split' in pitfalls_section.lower()
  assert 'merge' in pitfalls_section.lower()
  assert 'surrounding' in pitfalls_section.lower()
  assert 'confidence=5' in pitfalls_section


def test_extract_obligations_produces_one_entry_per_version():
  version = _version('draft', 'nopr')
  llm_extracted = extract_obligations(version)
  assert len(llm_extracted) == 1
  assert llm_extracted[0].obligation_type == 'procedural'
  # LlmExtractedObligation carries LlmCitation with citing_span, not offsets
  assert llm_extracted[0].citation.citing_span
  grounded = ground_obligations(llm_extracted, version)
  assert len(grounded) == 1
  assert grounded[0].citation.start_offset == 0
  assert grounded[0].citation.end_offset == len(llm_extracted[0].citation.citing_span)


def test_mapping_rubrics_contains_only_confidence_action_and_routing():
  # Rubrics is just MAPPING_CONFIDENCE_RUBRIC + RECOMMENDED_ACTION_RUBRIC +
  # ROUTING_DESTINATION_GLOSSARY. The old "Additional rules" subsection was
  # duplicated across Field Intents, so it was removed. The one
  # load-bearing rule (index-based selection prevents id hallucination)
  # is preserved in Field Intents under affected_items[*].item_index.
  change = _change('tightened')
  prompt = build_mapping_prompt(
    change, obligations=[], projects=[], documents=[], candidate_set_size=50
  )
  _, user = prompt.render()
  rubrics_section = user.split('## Rubrics')[1].split('## Pitfalls')[0]
  assert 'Additional rules' not in rubrics_section
  field_intents_section = user.split('## Field Intents')[1].split('## JSON Output Example')[0]
  assert 'prevents id hallucination' in field_intents_section


def test_mapping_prompt_proposes_per_item_action_and_route():
  # The prompt must emit per-item proposed_action + proposed_route on
  # each affected item. These replace the old mapping-level
  # recommended_action + routing_destination fields.
  change = _change('tightened')
  prompt = build_mapping_prompt(
    change, obligations=[], projects=[], documents=[], candidate_set_size=50
  )
  _, user = prompt.render()
  field_intents_section = user.split('## Field Intents')[1].split('## JSON Output Example')[0]
  assert 'affected_items[*].proposed_action' in field_intents_section
  assert 'affected_items[*].proposed_route' in field_intents_section
  # The JSON output example lists per-item action/route and omits
  # mapping-level recommended_action / routing_destination.
  json_example_section = user.split('## JSON Output Example')[1].split('## Inputs')[0]
  assert '"proposed_action"' in json_example_section
  assert '"proposed_route"' in json_example_section
  assert '"recommended_action"' not in json_example_section
  assert '"routing_destination"' not in json_example_section


def test_mapping_prompt_example_4_shows_diverse_asset_routing():
  # Example 4 is the teaching case for per-item routing: one Change
  # that genuinely touches three teams. Without this example at
  # gpt-5.4-nano the LLM collapses all items to a single "primary
  # team" route. The three distinct route strings must all appear
  # inside Example 4.
  change = _change('tightened')
  prompt = build_mapping_prompt(
    change, obligations=[], projects=[], documents=[], candidate_set_size=50
  )
  _, user = prompt.render()
  details_section = user.split('## Details')[1].split('## Rubrics')[0]
  assert 'Example 4' in details_section
  example_4 = details_section.split('Example 4')[1]
  # Three distinct routes in the SAME example.
  assert 'route="legal"' in example_4
  assert 'route="market_ops"' in example_4
  assert 'route="it_metering"' in example_4
  # Explicit teaching that three teams on one Change is the point.
  assert 'Three teams on one Change' in example_4


def test_mapping_prompt_pitfalls_reject_hedge_verbs_in_match_rationale():
  # Observed failure mode at gpt-5.4-nano on §(A) tightened change: the
  # model emitted TWO borderline items at match_confidence=3 whose
  # match_rationales led with hedge verbs ("may require updating...",
  # "should reflect the updated..."). The emission-floor-at-3 rubric
  # permitted them literally, but the Pitfall was designed to reject
  # hedge-shaped rationales regardless of the number attached. The
  # explicit hedge-verb language closes that loophole.
  change = _change('tightened')
  prompt = build_mapping_prompt(
    change, obligations=[], projects=[], documents=[], candidate_set_size=50
  )
  _, user = prompt.render()
  pitfalls_section = user.split('## Pitfalls')[1].split('## Field Intents')[0]
  assert 'HEDGE VERBS' in pitfalls_section
  # Specific hedge verbs that showed up in the observed failure.
  assert 'may require' in pitfalls_section
  assert 'should reflect' in pitfalls_section


def test_mapping_prompt_uses_mapping_confidence_rubric_not_generic():
  # The generic CONFIDENCE_LEVEL_RUBRIC describes citation-support
  # confidence; mapping needs semantic-overlap confidence with an
  # emission floor at 3. The prompt must render the mapping-specific
  # rubric, NOT the generic one.
  change = _change('tightened')
  prompt = build_mapping_prompt(
    change, obligations=[], projects=[], documents=[], candidate_set_size=50
  )
  _, user = prompt.render()
  rubrics_section = user.split('## Rubrics')[1].split('## Pitfalls')[0]
  assert 'Mapping confidence rubric' in rubrics_section
  assert 'floor for emission is 3' in rubrics_section
  # The generic rubric's giveaway phrase MUST NOT appear.
  assert 'cited passages state the conclusion verbatim' not in rubrics_section


def test_mapping_prompt_includes_action_and_routing_rubrics():
  # The mapping prompt must embed RECOMMENDED_ACTION_RUBRIC and
  # ROUTING_DESTINATION_GLOSSARY so the LLM has concrete decision rules
  # for picking review/amend/no_op and market_ops/legal/it_metering.
  # Without these the LLM guesses from the change text's surface tone,
  # routing by keyword match rather than team ownership.
  change = _change('tightened')
  prompt = build_mapping_prompt(
    change, obligations=[], projects=[], documents=[], candidate_set_size=50
  )
  _, user = prompt.render()
  assert 'Recommended-action rubric' in user
  assert 'Routing-destination glossary' in user
  # Each action's distinguishing clause is in the rendered prompt.
  rubrics_section = user.split('## Rubrics')[1].split('## Pitfalls')[0]
  assert 'concrete update' in rubrics_section  # amend
  assert 'first-pass human read' in rubrics_section  # review
  assert 'omitted entirely' in rubrics_section  # no_op
  # Each team's ownership scope is in the rendered prompt.
  assert 'wholesale market bidding' in rubrics_section  # market_ops
  assert 'tariff filings' in rubrics_section  # legal
  assert 'telemetry' in rubrics_section.lower()  # it_metering


def test_mapping_prompt_has_steps_details_pitfalls_sections():
  # Scaffolding parity with the other prompts. The three teaching cases
  # are (a) narrow mapping (SEC Form X-17A-5 — one obligation + one
  # document), (b) empty mapping (HIPAA training — no_op for a utility
  # tenant), (c) fan-out mapping (FinCEN CTR tightening — four affected
  # items across types, with topical-adjacency misses omitted).
  change = _change('tightened')
  prompt = build_mapping_prompt(
    change, obligations=[], projects=[], documents=[], candidate_set_size=50
  )
  _, user = prompt.render()
  assert '## Steps' in user
  assert '## Details' in user
  assert '## Pitfalls' in user
  details_section = user.split('## Details')[1].split('## Rubrics')[0]
  # Adjacent-domain examples — not Order 2222 (don't teach to test).
  assert 'X-17A-5' in details_section  # SEC Example 1
  assert 'HIPAA' in details_section or 'HHS' in details_section  # HHS Example 2
  assert 'CTR' in details_section  # FinCEN Example 3
  assert 'DER aggregator' not in details_section  # Order 2222 teach-to-test guard
  # Pitfalls name the four failure modes.
  pitfalls_section = user.split('## Pitfalls')[1].split('## Field Intents')[0]
  assert 'topically adjacent' in pitfalls_section
  assert 'match_confidence' in pitfalls_section
  assert 'amend' in pitfalls_section
  assert 'proposed_route' in pitfalls_section


def test_map_change_translates_llm_index_response_into_item_ids(verdant_grid_seeded):
  del verdant_grid_seeded
  from src.ingest import ingest_company_context

  obligations, projects, documents = ingest_company_context('verdant_grid', 'prod')
  mapping = map_change(
    _change('added'),
    obligations=obligations,
    projects=projects,
    documents=documents,
  )
  valid_obligation_ids = {o.obligation_id for o in obligations}
  valid_project_ids = {p.project_id for p in projects}
  valid_document_ids = {d.document_id for d in documents}
  valid_by_type = {
    'obligation': valid_obligation_ids,
    'project': valid_project_ids,
    'document': valid_document_ids,
  }
  for item in mapping.affected_items:
    assert item.item_id in valid_by_type[item.item_type]


def test_enqueue_mapping_escalates_below_threshold():
  item = enqueue_mapping(_mapping(confidence=2), _verified_change(), _config(threshold=3))
  assert item.status == 'escalated'


def test_enqueue_mapping_pending_at_threshold():
  item = enqueue_mapping(_mapping(confidence=3), _verified_change(), _config(threshold=3))
  assert item.status == 'pending'


def test_enqueue_mapping_priority_increases_with_affected_items():
  bigger = _mapping(
    confidence=4,
    affected_items=(
      AffectedItem(
        item_type='obligation',
        item_id='o-1',
        match_confidence=4,
        match_rationale='r',
        proposed_action='amend',
        proposed_route='market_ops',
      ),
      AffectedItem(
        item_type='project',
        item_id='p-1',
        match_confidence=3,
        match_rationale='r',
        proposed_action='review',
        proposed_route='market_ops',
      ),
    ),
  )
  smaller = _mapping(confidence=4)
  change = _verified_change()
  bigger_item = enqueue_mapping(bigger, change, _config(threshold=3))
  smaller_item = enqueue_mapping(smaller, change, _config(threshold=3))
  assert bigger_item.priority > smaller_item.priority
  assert bigger_item.priority == 4 * 3  # confidence × (1 + affected_count)
  assert smaller_item.priority == 4 * 1


def test_enqueue_mapping_uses_citation_threshold_when_citation_unverified():
  config = DomainConfig(
    escalation=EscalationConfig(mapping_threshold=2, citation_threshold=5),
    mapping=MappingConfig(candidate_set_size=50, context_window_tokens=20000),
    citation=CitationConfig(verification_required=True, min_citation_span_chars=10),
    task_model_configs={},
  )
  unverified = Citation(
    passage_id='proc:v:§1:p1',
    start_offset=0,
    end_offset=10,
    verified=False,
    verification_rationale='unverified in test',
  )
  change = _change('tightened', prior_citation=unverified, new_citation=unverified)
  item = enqueue_mapping(_mapping(confidence=4), change, config)
  assert item.status == 'escalated'


def test_enqueue_mapping_uses_mapping_threshold_when_citations_verified():
  config = DomainConfig(
    escalation=EscalationConfig(mapping_threshold=2, citation_threshold=5),
    mapping=MappingConfig(candidate_set_size=50, context_window_tokens=20000),
    citation=CitationConfig(verification_required=True, min_citation_span_chars=10),
    task_model_configs={},
  )
  item = enqueue_mapping(_mapping(confidence=4), _verified_change(), config)
  assert item.status == 'pending'


def test_enqueue_mapping_raises_on_mismatched_change_id():
  with pytest.raises(ValueError, match='does not match'):
    enqueue_mapping(
      _mapping(confidence=4, change_id='c-1'),
      _change('tightened').model_copy(update={'change_id': 'c-OTHER'}),
      _config(threshold=3),
    )


def test_pipeline_end_to_end_produces_expected_counts(verdant_grid_seeded):
  result = run_pipeline('verdant_grid', 'prod', dry_run=False)
  assert len(result.versions) == 2
  assert len(result.obligations) == 8
  assert len(result.projects) == 3
  assert len(result.documents) == 5
  assert len(result.changes) == 3
  assert len(result.extracted) == 2
  assert len(result.mappings) == 3
  assert len(result.review_items) == 3


def test_pipeline_persists_review_items_under_tenant_env_path(verdant_grid_seeded):
  run_pipeline('verdant_grid', 'prod', dry_run=False)
  path = verdant_grid_seeded / 'verdant_grid' / 'prod' / 'review_items.json'
  assert path.is_file()


def test_pipeline_dry_run_persists_nothing_under_env_dir(verdant_grid_seeded):
  run_pipeline('verdant_grid', 'prod', dry_run=True)
  env_dir = verdant_grid_seeded / 'verdant_grid' / 'prod'
  if env_dir.exists():
    assert list(env_dir.iterdir()) == []


def _asset_input(index, decision='approved', action='amend', route='legal', rationale='ok'):
  return {
    'affected_item_index': index,
    'decision': decision,
    'action': action,
    'route': route,
    'rationale': rationale,
  }


def test_apply_asset_decisions_single_approve_creates_decision_and_updates_item(
  verdant_grid_seeded,
):
  from src.review_queue import apply_asset_decisions
  from src.stores import asset_decision_store, mapping_store, review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  items_before = review_item_store.load_all('verdant_grid', 'prod')
  pending = next(i for i in items_before if i.status == 'pending')
  mapping = next(
    m for m in mapping_store.load_all('verdant_grid', 'prod') if m.mapping_id == pending.mapping_id
  )
  # Deciding ONE affected_item leaves the item in 'pending' because there
  # are still undecided pairs.
  updated = apply_asset_decisions(
    tenant_id='verdant_grid',
    environment='prod',
    review_item_id=pending.review_item_id,
    inputs=[
      _asset_input(
        0,
        decision='approved',
        action=mapping.affected_items[0].proposed_action,
        route=mapping.affected_items[0].proposed_route,
        rationale='looks good',
      )
    ],
    actor='rachel',
  )
  assert updated.status == 'pending'  # other items still undecided
  decisions = asset_decision_store.load_all('verdant_grid', 'prod')
  mine = [d for d in decisions if d.review_item_id == pending.review_item_id]
  assert len(mine) == 1
  assert mine[0].affected_item_index == 0
  assert mine[0].decision == 'approved'
  assert mine[0].actor == 'rachel'
  assert mine[0].item_id == mapping.affected_items[0].item_id


def test_apply_asset_decisions_approves_all_items_folds_to_approved(verdant_grid_seeded):
  from src.review_queue import apply_asset_decisions
  from src.stores import mapping_store, review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  mapping = next(
    m for m in mapping_store.load_all('verdant_grid', 'prod') if m.mapping_id == pending.mapping_id
  )
  inputs = [
    _asset_input(i, decision='approved', action=a.proposed_action, route=a.proposed_route)
    for i, a in enumerate(mapping.affected_items)
  ]
  updated = apply_asset_decisions(
    tenant_id='verdant_grid',
    environment='prod',
    review_item_id=pending.review_item_id,
    inputs=inputs,
    actor='rachel',
  )
  assert updated.status == 'approved'
  assert updated.priority == mapping.confidence  # pending_count=0 → priority = confidence * 1


def test_apply_asset_decisions_escalation_wins_fold(verdant_grid_seeded):
  from src.review_queue import apply_asset_decisions
  from src.stores import mapping_store, review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  mapping = next(
    m for m in mapping_store.load_all('verdant_grid', 'prod') if m.mapping_id == pending.mapping_id
  )
  inputs = []
  for i, a in enumerate(mapping.affected_items):
    decision = 'escalated' if i == 0 else 'approved'
    inputs.append(
      _asset_input(i, decision=decision, action=a.proposed_action, route=a.proposed_route)
    )
  updated = apply_asset_decisions(
    tenant_id='verdant_grid',
    environment='prod',
    review_item_id=pending.review_item_id,
    inputs=inputs,
    actor='rachel',
  )
  assert updated.status == 'escalated'


def test_apply_asset_decisions_override_wins_over_approved(verdant_grid_seeded):
  from src.review_queue import apply_asset_decisions
  from src.stores import mapping_store, review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  mapping = next(
    m for m in mapping_store.load_all('verdant_grid', 'prod') if m.mapping_id == pending.mapping_id
  )
  inputs = []
  for i, a in enumerate(mapping.affected_items):
    decision = 'overridden' if i == 0 else 'approved'
    inputs.append(
      _asset_input(i, decision=decision, action=a.proposed_action, route=a.proposed_route)
    )
  updated = apply_asset_decisions(
    tenant_id='verdant_grid',
    environment='prod',
    review_item_id=pending.review_item_id,
    inputs=inputs,
    actor='rachel',
  )
  assert updated.status == 'overridden'


def test_apply_asset_decisions_bulk_writes_multiple_decisions_in_one_call(
  verdant_grid_seeded,
):
  from src.review_queue import apply_asset_decisions
  from src.stores import asset_decision_store, mapping_store, review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  mapping = next(
    m for m in mapping_store.load_all('verdant_grid', 'prod') if m.mapping_id == pending.mapping_id
  )
  # Bulk-decide the first two items in one call.
  inputs = [
    _asset_input(0, decision='approved', action=mapping.affected_items[0].proposed_action,
                 route=mapping.affected_items[0].proposed_route),
    _asset_input(1, decision='dismissed', action=mapping.affected_items[1].proposed_action,
                 route=mapping.affected_items[1].proposed_route),
  ]
  apply_asset_decisions(
    tenant_id='verdant_grid',
    environment='prod',
    review_item_id=pending.review_item_id,
    inputs=inputs,
    actor='rachel',
  )
  decisions = asset_decision_store.load_all('verdant_grid', 'prod')
  mine = sorted(
    (d for d in decisions if d.review_item_id == pending.review_item_id),
    key=lambda d: d.affected_item_index,
  )
  assert [d.affected_item_index for d in mine] == [0, 1]
  assert mine[0].decision == 'approved'
  assert mine[1].decision == 'dismissed'


def test_apply_asset_decisions_priority_decreases_as_decisions_land(verdant_grid_seeded):
  from src.review_queue import apply_asset_decisions
  from src.stores import mapping_store, review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  mapping = next(
    m for m in mapping_store.load_all('verdant_grid', 'prod') if m.mapping_id == pending.mapping_id
  )
  pending_count_at_enqueue = len(mapping.affected_items)
  assert pending.priority == mapping.confidence * (1 + pending_count_at_enqueue)
  # Land ONE decision — pending_count drops by 1 — priority drops.
  updated = apply_asset_decisions(
    tenant_id='verdant_grid',
    environment='prod',
    review_item_id=pending.review_item_id,
    inputs=[
      _asset_input(
        0,
        decision='approved',
        action=mapping.affected_items[0].proposed_action,
        route=mapping.affected_items[0].proposed_route,
      )
    ],
    actor='rachel',
  )
  assert updated.priority == mapping.confidence * pending_count_at_enqueue


def test_apply_asset_decisions_raises_on_unknown_review_item(verdant_grid_seeded):
  from src.review_queue import apply_asset_decisions

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  with pytest.raises(ValueError, match='review_item_id not found'):
    apply_asset_decisions(
      tenant_id='verdant_grid',
      environment='prod',
      review_item_id='does-not-exist',
      inputs=[_asset_input(0)],
      actor='x',
    )


def test_apply_asset_decisions_raises_on_out_of_range_index(verdant_grid_seeded):
  from src.review_queue import apply_asset_decisions
  from src.stores import review_item_store

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  pending = next(
    i for i in review_item_store.load_all('verdant_grid', 'prod') if i.status == 'pending'
  )
  with pytest.raises(ValueError, match='out of range'):
    apply_asset_decisions(
      tenant_id='verdant_grid',
      environment='prod',
      review_item_id=pending.review_item_id,
      inputs=[_asset_input(99)],
      actor='x',
    )


def test_apply_asset_decisions_raises_on_empty_inputs(verdant_grid_seeded):
  from src.review_queue import apply_asset_decisions

  run_pipeline('verdant_grid', 'prod', dry_run=False)
  with pytest.raises(ValueError, match='at least one input'):
    apply_asset_decisions(
      tenant_id='verdant_grid',
      environment='prod',
      review_item_id='any-id',
      inputs=[],
      actor='x',
    )


def test_pipeline_isolates_tenants(verdant_grid_seeded):
  import shutil

  shutil.copytree(
    verdant_grid_seeded / 'verdant_grid',
    verdant_grid_seeded / 'other_tenant',
  )
  run_pipeline('verdant_grid', 'prod', dry_run=False)
  verdant_env = verdant_grid_seeded / 'verdant_grid' / 'prod'
  other_env = verdant_grid_seeded / 'other_tenant' / 'prod'
  assert verdant_env.is_dir()
  assert not other_env.exists()
