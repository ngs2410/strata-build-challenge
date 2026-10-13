from pydantic import BaseModel, ConfigDict


class LlmCitation(BaseModel):
  model_config = ConfigDict(extra='forbid')
  passage_id: str
  citing_span: str
