"""Keep the accepted sales-v1 model contract stable while adding new versions."""

import json
from pathlib import Path

from app.diagnosis.inputs import digest
from app.diagnosis.planner import SYSTEM_PROMPT, prompt
from app.diagnosis.schemas import SALES_TOOLS, DiagnosticReport, StepProposal


def test_sales_v1_schema_catalog_and_model_prompt_remain_byte_compatible():
    golden = json.loads(Path("tests/fixtures/diagnosis_v1_contract.json").read_text())
    catalog = {k: v.model_json_schema() for k, v in SALES_TOOLS.items()}
    task = {
        "id": "task-fixed",
        "query": "净销售额下降涉及哪些商品？",
        "parameters": {
            "budget": {
                "max_tool_calls": 10,
                "max_model_calls": 12,
                "max_tokens": 60000,
                "max_runtime_seconds": 180,
                "max_attempts": 3,
            }
        },
    }
    bundle = {
        "bundle_id": "bundle-fixed",
        "bundle_hash": "0" * 64,
        "period": {"start": "2026-09-01", "end": "2026-09-01"},
        "comparison_period": {"start": "2026-08-01", "end": "2026-08-01"},
        "sample": True,
    }
    actual = {
        "step_schema_hash": digest(StepProposal.model_json_schema()),
        "report_schema_hash": digest(DiagnosticReport.model_json_schema()),
        "catalog_hash": digest(catalog),
        "system_prompt_hash": digest(SYSTEM_PROMPT),
        "messages_hash": digest(
            prompt(task, bundle, {"skus": ["A", "B"], "objective": None}, [], [], catalog)
        ),
    }
    assert actual == golden


def test_extended_v2_schema_catalog_intent_and_model_prompt_remain_byte_compatible():
    from app.diagnosis.contracts import V2, tool_schemas
    from app.diagnosis.planner import unsupported_question
    from app.diagnosis.planner_v2 import SYSTEM_PROMPT_V2, objectives, prompt
    from app.diagnosis.schemas_v2 import DiagnosticReportV2, StepProposalV2

    golden = json.loads(Path("tests/fixtures/diagnosis_v2_contract.json").read_text())
    task = {
        **V2,
        "id": "task-fixed",
        "parameters": {
            "sources": ["sales", "inventory", "documents"],
            "budget": {
                "max_tool_calls": 10,
                "max_model_calls": 12,
                "max_tokens": 60000,
                "max_runtime_seconds": 180,
                "max_attempts": 3,
            },
        },
    }
    bundle = {
        "bundle_id": "bundle-fixed",
        "bundle_hash": "0" * 64,
        "period": {"start": "2026-09-01", "end": "2026-09-01"},
        "comparison_period": {"start": "2026-08-01", "end": "2026-08-01"},
        "sample": True,
    }
    catalog = {k: v.model_json_schema() for k, v in tool_schemas(task).items()}
    actual = {
        "step_schema_hash": digest(StepProposalV2.model_json_schema()),
        "report_schema_hash": digest(DiagnosticReportV2.model_json_schema()),
        "catalog_hash": digest(catalog),
        "system_prompt_hash": digest(SYSTEM_PROMPT_V2),
        "queries": {},
    }
    for query in golden["queries"]:
        actual["queries"][query] = {
            "objectives": objectives(query),
            "unsupported": unsupported_question(query),
            "messages_hash": digest(
                prompt(
                    {**task, "query": query},
                    bundle,
                    {"skus": ["Q"], "objective": None},
                    [],
                    [],
                    catalog,
                )
            ),
        }
    assert actual == golden
