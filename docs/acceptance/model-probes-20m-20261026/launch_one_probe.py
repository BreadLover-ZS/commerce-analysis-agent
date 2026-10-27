"""Authorize one probe from verified local readiness; load only the configured project key."""

import asyncio
import json
import os
from pathlib import Path

from app.core.config import Settings
from evaluations.diagnosis import implementation_manifest
from evaluations.diagnosis_probe import (
    load_parent,
    parser,
    require_prior_review,
    run,
    validate_probe_http_budget,
    validate_selection,
)
from evaluations.diagnosis_probe_accounting import LIMIT, file_hash
from evaluations.diagnosis_runtime import ROOT


def main():
    cli = parser()
    cli.add_argument("--readiness", type=Path, required=True)
    args = cli.parse_args()
    ready = json.loads(args.readiness.read_bytes())
    _, fingerprint = implementation_manifest()
    if (
        ready["implementation_sha256"] != fingerprint
        or ready["local_verification_passed"] is not True
    ):
        raise ValueError("local_readiness_does_not_match_current_source")
    for entry in ready["evidence_files"]:
        if file_hash(ROOT / entry["file"]) != entry["sha256"]:
            raise ValueError("local_verification_evidence_changed")
    network = json.loads((ROOT / ready["network_guard_file"]).read_bytes())
    if network["provider_requests"] != 0 or network["external_attempts_blocked"] != 0:
        raise ValueError("local_preflight_was_not_network_isolated")
    if args.max_total_tokens != LIMIT or not args.allow_paid:
        raise ValueError("explicit_20m_paid_authorization_required")
    parent = load_parent(args.parent)
    require_prior_review(parent, fingerprint, args.repair_evidence)
    case = validate_selection(args.cases, args.case_id, args.group, args.legacy_root)
    validate_probe_http_budget(case, args.group, args.max_http)
    if args.legacy_root.resolve() != Path(parent["legacy_root"]).resolve():
        raise ValueError("legacy_source_mismatch")
    # Only after all accounting, scope, source and local-verification gates pass.
    settings = Settings(_env_file=ROOT / ".env")
    if (
        settings.openai_base_url.rstrip("/") != "https://api.deepseek.com"
        or settings.llm_model != "deepseek-flash"
    ):
        raise ValueError("configured_project_key_destination_mismatch")
    secret = settings.openai_api_key.get_secret_value()
    if not secret:
        raise ValueError("configured_project_key_missing")
    os.environ[args.api_key_env] = secret
    try:
        failure = asyncio.run(run(args))
    finally:
        os.environ.pop(args.api_key_env, None)
    raise SystemExit(1 if failure else 0)


if __name__ == "__main__":
    main()
