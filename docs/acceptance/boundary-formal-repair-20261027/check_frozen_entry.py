"""Actual frozen packet admission check, with credential and socket sentinels."""
import json
import socket
import sys
from pathlib import Path

from evaluations import diagnosis_formal as formal
from evaluations import diagnosis_formal_plan as planning
from evaluations.diagnosis_probe_accounting import file_hash

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
PACKET = ROOT / "docs/acceptance/diagnosis-boundary-v2-formal-U-20261027"


def main():
    import app.core.config as config

    plan = planning.check(PACKET)
    path = PACKET / "execution-authorization-request.json"
    request = json.loads(path.read_bytes())
    request.update(plan_sha256=file_hash(PACKET / "manifest.json"),
                   cases_sha256=plan["packet_files_sha256"]["cases.json"],
                   implementation_sha256=plan["implementation_sha256"], model=plan["model"],
                   parent_anchor_receipt_sha256=plan["parent_accounting"]["parent_receipt_sha256"])
    assert request["granted"] is False and request["new_packet_transmission_granted"] is False
    path.write_text(json.dumps(request, indent=2) + "\n")
    output = ROOT / "evaluations/results/U-boundary-v2-unauthorized-must-not-exist"
    assert not output.exists()
    counters = {"credential_loads": 0, "socket_attempts": 0}

    def key(*a, **kw):
        counters["credential_loads"] += 1
        raise AssertionError("key read before authorization")

    def network(*a, **kw):
        counters["socket_attempts"] += 1
        raise AssertionError("network forbidden")

    old_settings, old_live = config.Settings, formal.live_configuration
    old_socket, old_argv = socket.socket.connect, sys.argv
    try:
        config.Settings = formal.live_configuration = key
        socket.socket.connect = network
        sys.argv = ["diagnosis_formal", "run", "--plan", str(PACKET),
                    "--parent", plan["parent_anchor"], "--slot-id", plan["slots"][0]["slot_id"],
                    "--output", str(output), "--authorization", str(path), "--allow-paid",
                    "--max-total-tokens", "20000000", "--use-project-key"]
        try:
            formal.main()
        except ValueError as error:
            reason = str(error)
        else:
            raise AssertionError("ungranted request must not execute")
        assert reason == "formal_explicit_authorization_required", reason
        assert counters == {"credential_loads": 0, "socket_attempts": 0}
        assert not output.exists()
    finally:
        config.Settings, formal.live_configuration = old_settings, old_live
        socket.socket.connect, sys.argv = old_socket, old_argv
    record = {"status": "PASS_UNAUTHORIZED_SEND_BLOCKED", "reason": reason,
              "plan_sha256": file_hash(PACKET / "manifest.json"), **counters,
              "provider_requests": 0, "fresh_task_graph_tool_executions": 0,
              "output_created": False, "real_parent_claim_created": False}
    (HERE / "frozen-entry-denial.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
