'''Pre-grounding citation shape — what the LLM returns before Python resolves offsets.

Two-step citation pattern (project depth area):
  1. LLM emits `LlmCitation(passage_index, citing_span)` — a 0-based index
     into the enumerated passage list in the prompt, plus the SHORTEST
     verbatim substring copied from that passage that supports the claim.
     The LLM NEVER emits numeric offsets (counting characters is a reliable
     LLM failure mode) and NEVER emits the full passage text when a tighter
     substring would do (prompts tell callers the "shortest verbatim span"
     convention; see per-task `field_intents`).
  2. `citation_grounding._resolve` looks up `passages[passage_index]`,
     substring-matches `citing_span` into `passage.text`, and computes
     `(start_offset, end_offset)` deterministically in Python, yielding a
     `Citation` (defined in `src.config`) with the real structural
     `passage_id` stamped from the resolved passage.

Why a separate type from `Citation`: `LlmCitation` has NO offsets, NO
`verified` field, and references passages by index rather than opaque id —
all three are results of post-LLM processing or avoid known LLM failure
modes. Mixing the two shapes would invite the LLM to try to fill
offsets / echo long passage_ids (both of which it would get wrong).
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
