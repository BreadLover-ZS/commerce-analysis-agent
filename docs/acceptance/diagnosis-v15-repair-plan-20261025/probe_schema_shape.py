"""Minimal local proposal-shape prototype; not production v15 implementation."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reference(Strict):
    evidence_id: str
    chunk_id: str
    quote_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class Interpretation(Strict):
    text: str
    references: list[Reference] = Field(min_length=1)
    status: Literal["unverified"] = "unverified"


class Tool(Strict):
    action: Literal["tool"]
    tool_name: str
    arguments: dict


class Clarify(Strict):
    action: Literal["clarify"]


class SalesStop(Strict):
    action: Literal["stop"]


class DocumentStop(Strict):
    action: Literal["stop"]
    rule_interpretations: list[Interpretation] = Field(default_factory=list, max_length=5)


class SalesProposal(
    RootModel[Annotated[Tool | SalesStop | Clarify, Field(discriminator="action")]]
):
    pass


class DocumentProposal(
    RootModel[Annotated[Tool | DocumentStop | Clarify, Field(discriminator="action")]]
):
    pass


def main(path):
    checks = []
    quote = {
        "text": "SENSITIVE_SAMPLE",
        "references": [{"evidence_id": "E-known", "chunk_id": "C-known", "quote_hash": "1" * 64}],
        "status": "unverified",
    }
    tool = {"action": "tool", "tool_name": "inventory_risk", "arguments": {}}
    vectors = [
        ("sales_tool", SalesProposal, tool, True),
        (
            "sales_tool_forbids_annotation",
            SalesProposal,
            {**tool, "rule_interpretations": [quote]},
            False,
        ),
        ("document_tool", DocumentProposal, tool, True),
        (
            "document_tool_forbids_annotation",
            DocumentProposal,
            {**tool, "rule_interpretations": [quote]},
            False,
        ),
        (
            "sales_stop_forbids_annotation",
            SalesProposal,
            {"action": "stop", "rule_interpretations": [quote]},
            False,
        ),
        (
            "document_stop_allows_structural_annotation",
            DocumentProposal,
            {"action": "stop", "rule_interpretations": [quote]},
            True,
        ),
        (
            "document_stop_requires_nonempty_refs",
            DocumentProposal,
            {"action": "stop", "rule_interpretations": [{**quote, "references": []}]},
            False,
        ),
        (
            "document_stop_forbids_verified",
            DocumentProposal,
            {"action": "stop", "rule_interpretations": [{**quote, "status": "verified"}]},
            False,
        ),
        (
            "clarify_forbids_annotation",
            DocumentProposal,
            {"action": "clarify", "rule_interpretations": [quote]},
            False,
        ),
        ("unknown_action", SalesProposal, {"action": "execute_sql"}, False),
        ("extra_field", SalesProposal, {**tool, "run_arbitrary_url": "SENSITIVE_SAMPLE"}, False),
    ]
    for name, schema, value, expected in vectors:
        diagnostic = None
        try:
            parsed = schema.model_validate_json(json.dumps(value))
            accepted = True
            assert (
                isinstance(parsed.model_dump(), dict)
                and parsed.model_dump()["action"] == value["action"]
            )
        except ValidationError as exc:
            accepted = False
            diagnostic = exc.errors(include_input=False, include_context=False, include_url=False)
            assert "SENSITIVE_SAMPLE" not in json.dumps(diagnostic)
        assert accepted is expected, name
        checks.append(
            {
                "check": name,
                "passed": True,
                "accepted": accepted,
                "expected": expected,
                "safe_errors": diagnostic,
            }
        )
    sales_schema = SalesProposal.model_json_schema()
    assert "Interpretation" not in sales_schema.get("$defs", {})
    assert all(
        "rule_interpretations" not in definition.get("properties", {})
        for definition in sales_schema.get("$defs", {}).values()
    )
    checks.append(
        {"check": "sales_schema_does_not_expose_rule_fields_or_reference_types", "passed": True}
    )
    first = hashlib.sha256(
        json.dumps(SalesProposal.model_json_schema(), sort_keys=True).encode()
    ).hexdigest()
    second = hashlib.sha256(
        json.dumps(SalesProposal.model_json_schema(), sort_keys=True).encode()
    ).hexdigest()
    assert first == second
    checks.append({"check": "same_frozen_sources_schema_hash_stable", "passed": True})
    result = {
        "status": "MINIMAL_SCHEMA_FEASIBILITY_ONLY_NOT_PRODUCTION_REPAIR",
        "passed": len(checks),
        "failed": 0,
        "provider_requests": 0,
        "checks": checks,
        "limitations": [
            "No provider JSON reliability measured.",
            "DocumentStop still requires server-side exact quote/source/ownership validation.",
            "Full proposal fields, saved-version routing, bounded correction and durable replay need implementation tests.",
        ],
    }
    with path.open("x") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(result["status"], result["passed"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
