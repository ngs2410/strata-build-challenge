'''Pre-grounding citation shape — what the LLM returns before Python resolves offsets.

Two-step citation pattern (project depth area):
  1. LLM emits `LlmCitation(passage_id, citing_span)` — a verbatim text
     substring copied from the cited passage. The LLM NEVER emits numeric
     offsets; counting characters is a reliable LLM failure mode.
  2. `citation_grounding._resolve` substring-matches `citing_span` into
     `passage.text` and computes `(start_offset, end_offset)` deterministically
     in Python, yielding a `Citation` (defined in `src.config`).

Why a separate type from `Citation`: `LlmCitation` has NO offsets and NO
`verified` field — those are results of post-LLM processing. Mixing the two
shapes would invite the LLM to try to fill offsets (which it would get wrong).
The LLM response models (`LlmChange`, `LlmExtractedObligation`) use
`LlmCitation`; everything downstream uses `Citation`. The grounding step is
the one-way bridge between them.

Lives in its own module to break what would otherwise be a circular import
between `src.version_diff`, `src.obligation_extraction`, and
`src.citation.citation_grounding` (CLAUDE.md "move commonly needed code with
no dependencies to `*_utils.py` or shared modules").
'''

from pydantic import BaseModel, ConfigDict


class LlmCitation(BaseModel):
  model_config = ConfigDict(extra='forbid')
  passage_index: int
  citing_span: str
