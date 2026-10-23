"""v13 report extension; old report schemas and saved checkpoints stay frozen."""

from typing import Literal

from pydantic import Field

from app.diagnosis.schemas import StrictModel
from app.diagnosis.schemas_v2 import DiagnosticReportV2


class GapSummary(StrictModel):
    total: int = Field(ge=0, strict=True)
    displayed: int = Field(ge=0, strict=True)
    omitted: int = Field(ge=0, strict=True)


class DiagnosticReportV3(DiagnosticReportV2):
    schema_version: Literal[3] = 3
    request_contract: dict
    request_obligations: list[dict] = Field(max_length=2000)
    open_question_details: list[str] = Field(max_length=2000)
    gap_summary: GapSummary
