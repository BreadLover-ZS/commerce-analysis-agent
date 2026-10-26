"""v15 report: execution stop, source availability and answer completion are distinct."""

from typing import Literal

from pydantic import Field

from app.diagnosis.schemas_v3 import DiagnosticReportV3


class DiagnosticReportV4(DiagnosticReportV3):
    schema_version: Literal[4] = 4
    answer_completion: dict
    gap_records: list[dict] = Field(max_length=2000)
    capability_witnesses: list[dict] = Field(max_length=20)
