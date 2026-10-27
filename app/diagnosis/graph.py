"""One bounded LangGraph agent on the existing durable Task thread and ledgers."""

import asyncio
import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from app.core.errors import ReportValidationFailed, UnsupportedGraphVersion
from app.core.files import safe_path
from app.diagnosis.contracts import (
    RUNTIME_VERSIONS,
    V3,
    V4,
    V5,
    V6,
    V7,
    V8,
    V9,
    V10,
    V11,
    V12,
    V13,
    V14,
    V15,
    V16,
    extended_objectives,
    is_extended,
    tool_schemas,
)
from app.diagnosis.metrics import DiagnosticMetrics
from app.diagnosis.model_calls import DiagnosisModelGateway
from app.diagnosis.planner import (
    demo_proposal,
    initial_scope,
    missing_coverage,
    objectives,
    prompt,
    unsupported_question,
)
from app.diagnosis.reporting import display_gaps, make_report, markdown
from app.diagnosis.schemas import (
    StepProposal,
)
from app.diagnosis.store import STEP_FINAL, DiagnosisStore
from app.diagnosis.tools import DiagnosisToolGateway
from app.diagnosis.validation import information_digest, observations
from app.research.agents import model_from_settings
from app.storage.repository import BudgetExceeded, Conflict
from app.utils.word_converter import convert_md_to_pdf

RECURSION_LIMIT = 8 * 6 + 12  # Independent persisted 8-step limit is the actual guard.


def argument_feedback(error, schema):
    """Bounded schema hints, without rejected values, raw keys or error bodies."""
    if isinstance(error, ValidationError):
        known = set(schema.model_fields) if schema else set()
        fields = []
        for failure in error.errors(include_url=False, include_context=False, include_input=False)[
            :5
        ]:
            location = []
            for index, part in enumerate(failure["loc"]):
                allowed = known if index == 0 else {"skus", "categories"}
                location.append(
                    part if part in allowed else "<item>" if isinstance(part, int) else "<extra>"
                )
            fields.append({"field": ".".join(location) or "arguments", "type": failure["type"]})
        return {"code": "argument_schema", "fields": fields}
    return {"code": "tool_not_available" if isinstance(error, KeyError) else "scope_not_permitted"}


class DiagnosisState(TypedDict, total=False):
    task_id: str
    bundle_id: str
    graph_version: str
    objective: str
    scope: dict
    plan_version: int
    sequence: int
    steps: list[dict]
    current_step: dict
    evidence_refs: list[str]
    finding_refs: list[str]
    seen_information: list[str]
    no_information_count: int
    open_questions: list[str]
    stop_reason: str
    report: dict
    validation: dict
    report_hash: str
    report_version: int
    status: str
    approved: bool


def build_diagnosis(repo, settings, ctx, checkpointer, model=None):
    task = repo.get_task(ctx.task_id)
    expected = RUNTIME_VERSIONS.get(task["graph_version"])
    if expected is None or any(task[k] != v for k, v in expected.items()):
        raise UnsupportedGraphVersion()
    if (
        task["graph_version"] == V16["graph_version"]
        and task["parameters"].get("accounting_protocol") != 2
    ):
        raise UnsupportedGraphVersion()
    proposal_schema = StepProposal
    propose_fn, prompt_fn, gaps_fn, objectives_fn = (
        demo_proposal,
        prompt,
        missing_coverage,
        objectives,
    )
    findings_fn, information_fn = observations, information_digest
    unsupported_fn, extensions_fn = unsupported_question, extended_objectives
    v3 = task["graph_version"] in {
        v["graph_version"] for v in (V3, V4, V5, V6, V7, V8, V9, V10, V11, V12, V13, V14, V15, V16)
    }
    v4 = task["graph_version"] in {
        v["graph_version"] for v in (V4, V5, V6, V7, V8, V9, V10, V11, V12, V13, V14, V15, V16)
    }
    if is_extended(task):
        from app.diagnosis import planner_v2, validation_v2
        from app.diagnosis.schemas_v2 import StepProposalV2

        proposal_schema = StepProposalV2
        propose_fn, prompt_fn = planner_v2.demo_proposal, planner_v2.prompt
        gaps_fn, objectives_fn = planner_v2.missing_coverage, planner_v2.objectives
        findings_fn, information_fn = validation_v2.observations, validation_v2.information_digest
    if v3:
        from app.diagnosis import planner_v3

        propose_fn, prompt_fn = planner_v3.demo_proposal, planner_v3.prompt
        gaps_fn, objectives_fn = planner_v3.missing_coverage, planner_v3.objectives
        unsupported_fn = planner_v3.unsupported_question

        def extensions_fn(query):
            return [g for g in objectives_fn(query) if g in {"inventory", "rules"}]

    if v4:
        from app.diagnosis import planner_v4

        prompt_fn = planner_v4.prompt
        computable_fn = planner_v4.computable_gap_tools
    if task["graph_version"] in {
        v["graph_version"] for v in (V5, V6, V7, V8, V9, V10, V11, V12, V13, V14, V15, V16)
    }:
        from app.diagnosis import planner_v5

        propose_fn, prompt_fn = planner_v5.demo_proposal, planner_v5.prompt
        gaps_fn, computable_fn = planner_v5.missing_coverage, planner_v5.computable_gap_tools
    if task["graph_version"] == V6["graph_version"]:
        from app.diagnosis import planner_v6

        prompt_fn = planner_v6.prompt
    if task["graph_version"] in {
        V7["graph_version"],
        V8["graph_version"],
        V9["graph_version"],
        V10["graph_version"],
        V11["graph_version"],
        V12["graph_version"],
        V13["graph_version"],
        V14["graph_version"],
        V15["graph_version"],
        V16["graph_version"],
    }:
        from app.diagnosis import planner_v7

        prompt_fn, gaps_fn, objectives_fn = (
            planner_v7.prompt,
            planner_v7.missing_coverage,
            planner_v7.objectives,
        )
    if task["graph_version"] in {
        V8["graph_version"],
        V9["graph_version"],
        V10["graph_version"],
        V11["graph_version"],
        V12["graph_version"],
        V13["graph_version"],
        V14["graph_version"],
        V15["graph_version"],
        V16["graph_version"],
    }:
        from app.diagnosis import planner_v8

        propose_fn, prompt_fn = planner_v8.demo_proposal, planner_v8.prompt
    if task["graph_version"] in {
        V9["graph_version"],
        V10["graph_version"],
        V11["graph_version"],
        V12["graph_version"],
        V13["graph_version"],
        V14["graph_version"],
        V15["graph_version"],
        V16["graph_version"],
    }:
        from app.diagnosis import planner_v9

        propose_fn, prompt_fn = planner_v9.demo_proposal, planner_v9.prompt
        gaps_fn, computable_fn = planner_v9.missing_coverage, planner_v9.computable_gap_tools

    if task["graph_version"] == V10["graph_version"]:
        from app.diagnosis import planner_v10

        propose_fn, prompt_fn = planner_v10.demo_proposal, planner_v10.prompt
        gaps_fn, computable_fn = planner_v10.missing_coverage, planner_v10.computable_gap_tools

    if task["graph_version"] == V11["graph_version"]:
        from app.diagnosis import planner_v10, planner_v11

        propose_fn, prompt_fn = planner_v11.demo_proposal, planner_v11.prompt
        gaps_fn, computable_fn = planner_v11.missing_coverage, planner_v11.computable_gap_tools
        objectives_fn, unsupported_fn = planner_v11.objectives, planner_v11.unsupported_question

    if task["graph_version"] == V12["graph_version"]:
        from app.diagnosis import planner_v10, planner_v12

        propose_fn, prompt_fn = planner_v12.demo_proposal, planner_v12.prompt
        gaps_fn, computable_fn = planner_v12.missing_coverage, planner_v12.computable_gap_tools
        objectives_fn, unsupported_fn = planner_v12.objectives, planner_v12.unsupported_question

    scope_fn = initial_scope
    if task["graph_version"] == V13["graph_version"]:
        from app.diagnosis import planner_v10, planner_v13

        propose_fn, prompt_fn = planner_v13.demo_proposal, planner_v13.prompt
        gaps_fn, computable_fn = planner_v13.missing_coverage, planner_v13.computable_gap_tools
        objectives_fn, unsupported_fn = planner_v13.objectives, planner_v13.unsupported_question
        scope_fn = planner_v13.initial_scope

    if task["graph_version"] == V14["graph_version"]:
        from app.diagnosis import planner_v10, planner_v14

        propose_fn, prompt_fn = planner_v14.demo_proposal, planner_v14.prompt
        gaps_fn, computable_fn = planner_v14.missing_coverage, planner_v14.computable_gap_tools
        objectives_fn, unsupported_fn = planner_v14.objectives, planner_v14.unsupported_question
        scope_fn = planner_v14.initial_scope

    v15 = task["graph_version"] in {V15["graph_version"], V16["graph_version"]}
    if v15:
        from app.commerce.answer_versions import boundary_version, completion_module
        from app.diagnosis import planner_v10, planner_v15
        from app.diagnosis.proposals_v3 import correction
        from app.diagnosis.proposals_v3 import proposal_schema as schema_factory

        if boundary_version(task["parameters"]) == 2:
            from app.diagnosis import planner_boundary_v2 as planner_v15
        completion = completion_module(task["parameters"]).completion
        proposal_schema = schema_factory(task)
        propose_fn, prompt_fn = planner_v15.demo_proposal, planner_v15.prompt
        if task["graph_version"] == V16["graph_version"]:
            from functools import partial

            from app.diagnosis.planner_v16 import prompt as v16_prompt

            prompt_fn = partial(v16_prompt, base_prompt=planner_v15.prompt)
        gaps_fn, computable_fn = planner_v15.missing_coverage, planner_v15.computable_gap_tools
        objectives_fn, unsupported_fn = planner_v15.objectives, planner_v15.unsupported_question
        scope_fn = planner_v15.initial_scope
        from app.diagnosis.reporting import report_contract

        findings_fn = report_contract(task)[1]

    schemas = tool_schemas(task)
    gateway, store = DiagnosisToolGateway(repo, ctx), DiagnosisStore(repo)
    if task["mode"] == "demo" and not (
        settings.app_env == "test" and getattr(model, "diagnosis_protocol_stub", False)
    ):
        # A deployment mode change or an injected real SDK client cannot make a
        # previously accepted demo task billable.
        model = None
    decision_mode = (
        "scripted_protocol"
        if model is not None and getattr(model, "diagnosis_protocol_stub", False)
        else "live_model"
        if task["mode"] == "live"
        else "demo_policy"
    )
    if task["mode"] == "live" and model is None:
        model = model_from_settings(settings)
    calls = (
        DiagnosisModelGateway(repo, ctx, model, settings.llm_model or type(model).__name__)
        if model
        else None
    )

    async def snapshot():
        return await asyncio.to_thread(store.snapshot, ctx)

    async def update(sequence, **values):
        return await asyncio.to_thread(store.update_step, ctx, sequence, **values)

    async def resolve_inputs(_):
        data = await snapshot()
        return {
            "task_id": ctx.task_id,
            "bundle_id": data["bundle"]["bundle_id"],
            "graph_version": task["graph_version"],
            "objective": task["query"],
            "scope": (
                scope_fn(task["query"], data["bundle"], data["sales_payload"]["products"])
                if task["graph_version"]
                in {V14["graph_version"], V15["graph_version"], V16["graph_version"]}
                else scope_fn(task["query"], data["bundle"])
            ),
            "plan_version": 1,
            "sequence": 0,
            "steps": [],
            "evidence_refs": [],
            "finding_refs": [],
            "seen_information": [],
            "no_information_count": 0,
            "open_questions": [],
            "status": "running",
        }

    async def baseline_inspect(state):
        try:
            result = await asyncio.to_thread(
                gateway.invoke, "inspect_dataset", {}, "baseline-inspect"
            )
        except BudgetExceeded:
            return {"stop_reason": "tool_budget"}
        return {
            "evidence_refs": result["evidence_ids"],
            "seen_information": [information_fn(result)],
        }

    async def baseline_compare(state):
        try:
            result = await asyncio.to_thread(
                gateway.invoke,
                "compare_periods",
                {"filters": {"skus": state["scope"]["skus"]}},
                "baseline-compare",
            )
        except BudgetExceeded:
            return {"stop_reason": "tool_budget"}
        value = {
            "evidence_refs": state["evidence_refs"] + result["evidence_ids"],
            "seen_information": state["seen_information"] + [information_fn(result)],
        }
        one_period_covered = task["graph_version"] == V14[
            "graph_version"
        ] and planner_v14.single_period_covered(task["query"], result)
        if v15 or one_period_covered:
            pass
        elif result["coverage"]["current"]["observed_records"] == 0:
            value.update(stop_reason="no_evidence", open_questions=["本期没有可核对的已记录事实"])
        elif not result["comparable"]:
            value.update(
                stop_reason="evidence_gap",
                open_questions=["两期数据覆盖不足，需补充原始数据并新建任务"],
            )
        elif unsupported_fn(task["query"]):
            value.update(
                stop_reason="evidence_gap",
                open_questions=(
                    gaps_fn(task["query"], [], state["scope"])
                    if task["graph_version"]
                    in {
                        V11["graph_version"],
                        V12["graph_version"],
                        V13["graph_version"],
                        V14["graph_version"],
                    }
                    else ["所问指标或操作超出当前数据与只读边界"]
                ),
            )
        if is_extended(task) and extensions_fn(task["query"]) and not unsupported_fn(task["query"]):
            value.pop("stop_reason", None)
            if v3 and set(objectives_fn(task["query"])) <= {
                "rules",
                "inventory",
                "rule_applicability",
            }:
                value.pop("open_questions", None)
        return value

    async def propose(state):
        data = await snapshot()
        if (
            v15
            and data["task"]["metrics"].get("usage_complete") is not False
            and not planner_v15.planner_v14.ambiguous_entity(task["query"], state["scope"])
        ):
            done = completion(task["query"], data["evidence"], state["scope"], data["steps"])
            if done["status"] in {"fulfilled", "boundary_explained"}:
                return {"stop_reason": "evidence_gap" if done["data_gaps"] else "target_covered"}
        if (
            task["graph_version"]
            in {V12["graph_version"], V13["graph_version"], V14["graph_version"]}
            and (
                planner_v14
                if task["graph_version"] == V14["graph_version"]
                else planner_v13
                if task["graph_version"] == V13["graph_version"]
                else planner_v12
            ).baseline_complete(task["query"], data["evidence"], state["scope"])
            and data["task"]["metrics"].get("usage_complete") is not False
        ):
            return {"stop_reason": "target_covered"}
        sequence = state["sequence"] + 1
        if sequence > 8:
            return {"stop_reason": "step_budget"}
        existing = next((s for s in data["steps"] if s["sequence"] == sequence), None)
        if existing:
            return {"sequence": sequence, "current_step": existing, "steps": data["steps"]}
        model_call_id = None
        invalid = False
        if calls:
            if task["parameters"]["budget"].get("stop_after_model_failure") and any(
                s["status"] in {"REJECTED", "FAILED"} for s in data["steps"]
            ):
                return {"stop_reason": "invalid_decision"}
            limit = min(
                12, settings.max_model_calls, task["parameters"]["budget"]["max_model_calls"]
            )
            from app.diagnosis.planner_budget import decision_limit as model_decision_limit

            decision_limit = model_decision_limit(limit, task["parameters"]["budget"])
            recovered = await calls.recover(proposal_schema, step_id=f"step-{sequence}")
            if recovered is None and data["task"]["metrics"]["model_calls"] >= decision_limit:
                return {"stop_reason": "model_budget"}
            try:
                result = (
                    recovered
                    if recovered is not None
                    else await calls.invoke(
                        proposal_schema,
                        prompt_fn(
                            data["task"]
                            if task["graph_version"]
                            in {
                                V12["graph_version"],
                                V13["graph_version"],
                                V14["graph_version"],
                                V15["graph_version"],
                                V16["graph_version"],
                            }
                            else task,
                            data["bundle"],
                            state["scope"],
                            data["steps"],
                            data["evidence"],
                            gateway.catalog(),
                        ),
                        step_id=f"step-{sequence}",
                    )
                )
            except BudgetExceeded:
                reason = (
                    "usage_unknown"
                    if not data["task"]["metrics"]["usage_complete"]
                    else "model_budget"
                )
                return {"stop_reason": reason}
            model_call_id = result["call_id"]
            if result["status"] == "NOT_SENT":
                return {
                    "stop_reason": "model_budget",
                    "open_questions": ["模型请求因预算在发送前停止，剩余义务未完成"],
                }
            if result["status"] == "UNKNOWN" or not result["usage_complete"]:
                return {
                    "stop_reason": "usage_unknown",
                    "open_questions": ["调用结果或用量未知，未继续发起模型请求"],
                }
            invalid = result["status"] != "SUCCEEDED"
            proposal = (
                result["output"]
                if not invalid
                else {
                    "action": "invalid",
                    "question": "模型提议未通过结构化校验",
                    "hypothesis_code": "data_coverage",
                }
            )
        else:
            proposal = propose_fn(task, state["scope"], data["evidence"], data["steps"]).model_dump(
                mode="json"
            )
        if task["graph_version"] in {
            V10["graph_version"],
            V11["graph_version"],
            V12["graph_version"],
            V13["graph_version"],
            V14["graph_version"],
            V15["graph_version"],
            V16["graph_version"],
        }:
            proposal = planner_v10.public_proposal(proposal)
        step = await asyncio.to_thread(store.put_step, ctx, sequence, proposal, model_call_id)
        if invalid:
            step = await update(
                sequence,
                status="REJECTED",
                assessment={
                    "error_code": "invalid_decision",
                    **({"correction": correction(result.get("error_code"))} if v15 else {}),
                },
            )
        return {"sequence": sequence, "current_step": step, "steps": [*data["steps"], step]}

    async def authorize(state):
        step, sequence = state["current_step"], state["sequence"]
        proposal = step["proposal"]
        if is_extended(task):
            data = await snapshot()
            if not validation_v2.valid_interpretations(
                proposal.get("rule_interpretations", []), data["evidence"]
            ):
                step = await update(
                    sequence,
                    status="REJECTED",
                    assessment={
                        "error_code": "unbound_rule_interpretation",
                        **(
                            {
                                "correction": correction(
                                    "unbound_rule_interpretation", data["evidence"]
                                )
                            }
                            if v15
                            else {}
                        ),
                    },
                )
                return {"current_step": step}
        if proposal["action"] == "stop":
            data = await snapshot()
            missing = (
                gaps_fn(
                    task["query"],
                    data["evidence"],
                    state["scope"],
                    [
                        {**s, "status": "SUCCEEDED"} if s["sequence"] == sequence else s
                        for s in data["steps"]
                    ],
                )
                if v15
                else gaps_fn(task["query"], data["evidence"], state["scope"])
            )
            reason = proposal["stop_reason"]
            if reason == "target_covered" and missing:
                if v4:
                    tools = computable_fn(
                        task["query"], data["evidence"], state["scope"], gateway.catalog()
                    )
                    if tools:
                        step = await update(
                            sequence,
                            status="REJECTED",
                            assessment={
                                "error_code": "incomplete_evidence",
                                "correction": {
                                    "code": "computable_evidence_gap",
                                    "required_evidence_gaps": missing,
                                    "available_tools": tools,
                                },
                            },
                        )
                        return {"current_step": step}
                reason = "evidence_gap"
            if (
                reason == "evidence_gap"
                and not missing
                and not unsupported_fn(task["query"])
                and task["graph_version"]
                in {
                    V9["graph_version"],
                    V10["graph_version"],
                    V11["graph_version"],
                    V12["graph_version"],
                    V13["graph_version"],
                    V14["graph_version"],
                }
            ):
                # v9：没有必填缺口、也未超出可回答范围时，声明的缺口无法用授权证据补齐；
                # 原文查阅不应因适用性未核验而被报成资料不足。
                reason = "target_covered"
            step = await update(
                sequence,
                status="SUCCEEDED",
                assessment={"next_action": "stop", "stop_reason": reason},
            )
            return {"stop_reason": reason, "open_questions": missing, "current_step": step}
        if proposal["action"] == "clarify":
            step = await update(sequence, status="AUTHORIZED")
            return {"current_step": step}
        schema = None
        try:
            schema = schemas[proposal["tool_name"]]
            args = schema.model_validate(proposal["arguments"])
            if hasattr(args, "filters"):
                if set(args.filters.skus) - set(state["scope"]["skus"]):
                    raise ValueError("scope_expansion")
                if not args.filters.skus:
                    args.filters.skus = state["scope"]["skus"]
                data = await snapshot()
                DiagnosticMetrics(data["sales_payload"], data["bundle"], args.filters)
            validated = args.model_dump(mode="json")
        except (KeyError, ValueError, ValidationError) as error:
            assessment = {"error_code": "tool_or_argument_rejected"}
            if v3:
                assessment["correction"] = argument_feedback(error, schema)
                if task["graph_version"] in {
                    V8["graph_version"],
                    V9["graph_version"],
                    V10["graph_version"],
                    V11["graph_version"],
                    V12["graph_version"],
                    V13["graph_version"],
                    V14["graph_version"],
                }:
                    from app.diagnosis.graph_constraints import SERIES_CONSTRAINT
                    from app.diagnosis.schemas import SeriesArgs

                    if schema is SeriesArgs and isinstance(error, ValidationError):
                        assessment["correction"]["constraints"] = [SERIES_CONSTRAINT]
            step = await update(sequence, status="REJECTED", assessment=assessment)
            return {"current_step": step}
        step = await update(sequence, status="AUTHORIZED", validated_args=validated)
        return {"current_step": step}

    async def execute_tool(state):
        step, sequence = state["current_step"], state["sequence"]
        if step["status"] in STEP_FINAL:
            return {}
        step = await update(sequence, status="RUNNING")
        try:
            result = await asyncio.to_thread(
                gateway.invoke,
                step["proposal"]["tool_name"],
                step["validated_args"],
                step["step_id"],
            )
        except BudgetExceeded:
            step = await update(sequence, status="FAILED", assessment={"error_code": "tool_budget"})
            return {"current_step": step, "stop_reason": "tool_budget"}
        success = result["status"] in {"ok", "empty", "incomparable"}
        status = (
            "REUSED"
            if success and result.get("reused")
            else "SUCCEEDED"
            if success
            else "FAILED"
            if result.get("call_id")
            else "REJECTED"
        )
        step = await update(
            sequence,
            status=status,
            call_id=result.get("call_id"),
            evidence_ids=result.get("evidence_ids", []),
        )
        return {"current_step": step}

    async def assess(state):
        data = await snapshot()
        step = next(s for s in data["steps"] if s["sequence"] == state["sequence"])
        current = [e for e in data["evidence"] if e["id"] in step["evidence_ids"]]
        new = [
            information_fn(e["payload"])
            for e in current
            if e["payload"]["status"] in ({"ok", "incomparable", "empty"} if v15 else {"ok"})
        ]
        fresh = bool(set(new) - set(state["seen_information"]))
        no_information = 0 if fresh else state["no_information_count"] + 1
        findings = findings_fn(ctx.task_id, data["evidence"], data["steps"])
        await asyncio.to_thread(store.save_findings, ctx, findings)
        matching = [
            f
            for f in findings
            if f["hypothesis_code"] == step["proposal"]["hypothesis_code"]
            and set(f["supporting_evidence"] + f["contradicting_evidence"])
            & set(step["evidence_ids"])
        ]
        supported = [f["finding_id"] for f in matching]
        hypothesis_status = (
            "supported_observation"
            if any(f["status"] == "supported_observation" for f in matching)
            else "contradicted"
            if any(f["status"] == "contradicted" for f in matching)
            else "insufficient"
        )
        assessment = {
            **(step["assessment"] or {}),
            "new_information": fresh,
            "finding_refs": supported,
            "hypothesis_status": hypothesis_status,
            "next_action": "propose",
        }
        rejected = data["steps"][-2:]
        stop = state.get("stop_reason")
        if len(rejected) == 2 and all(s["status"] == "REJECTED" for s in rejected):
            stop = stop or "invalid_decision"
        elif no_information >= (
            1 if calls and task["parameters"]["budget"].get("stop_after_model_failure") else 2
        ):
            stop = stop or "no_information"
        elif state["sequence"] >= 8:
            stop = stop or "step_budget"
        if task["graph_version"] in {
            V11["graph_version"],
            V12["graph_version"],
            V13["graph_version"],
            V14["graph_version"],
        }:
            from app.commerce.answer_contract import unsupported_gaps
            from app.diagnosis.planner_v11 import pending_computable

            if task["graph_version"] == V12["graph_version"]:
                pending_computable = planner_v12.pending_computable
            if task["graph_version"] == V13["graph_version"]:
                pending_computable = planner_v13.pending_computable
            if task["graph_version"] == V14["graph_version"]:
                from app.commerce.answer_contract_v3 import unsupported_gaps

                pending_computable = planner_v14.pending_computable

            if (
                (
                    data["task"]
                    if task["graph_version"]
                    in {V12["graph_version"], V13["graph_version"], V14["graph_version"]}
                    else task
                )["metrics"].get("usage_complete")
                is not False
                and stop != "usage_unknown"
                and not pending_computable(task["query"], data["evidence"], state["scope"])
            ):
                stop = (
                    "evidence_gap"
                    if (
                        unsupported_gaps(task["query"])
                        or display_gaps(task, data["evidence"], state["scope"])
                    )
                    else "target_covered"
                )
        if (
            v15
            and not stop
            and data["task"]["metrics"].get("usage_complete") is not False
            and not planner_v15.pending_computable(
                task["query"], data["evidence"], state["scope"], data["steps"]
            )
        ):
            stop = (
                "evidence_gap"
                if gaps_fn(task["query"], data["evidence"], state["scope"], data["steps"])
                or display_gaps(task, data["evidence"], state["scope"])
                else "target_covered"
            )
        if stop:
            assessment["next_action"] = "stop"
        step = await update(state["sequence"], assessment=assessment)
        value = {
            "current_step": step,
            "steps": [s if s["sequence"] != step["sequence"] else step for s in data["steps"]],
            "evidence_refs": [e["id"] for e in data["evidence"]],
            "finding_refs": [f["finding_id"] for f in findings],
            "seen_information": sorted(set(state["seen_information"] + new)),
            "no_information_count": no_information,
        }
        if stop:
            value["stop_reason"] = stop
        return value

    async def clarify(state):
        step, sequence = state["current_step"], state["sequence"]
        data = await snapshot()
        kind = step["proposal"]["clarification_scope"]
        if kind == "sku":
            options = state["scope"]["skus"]
        elif kind == "category":
            options = sorted(
                {
                    p["category"]
                    for p in data["sales_payload"]["products"]
                    if p["sku"] in state["scope"]["skus"]
                }
            )
        else:
            options = (
                [state["scope"]["objective"]]
                if state["scope"].get("objective")
                else objectives_fn(task["query"]) or ["net_revenue", "refunds", "products", "daily"]
            )
        if len(options) < 2:
            step = await update(
                sequence, status="REJECTED", assessment={"error_code": "no_remaining_ambiguity"}
            )
            return {"current_step": step}
        request = await asyncio.to_thread(
            store.ensure_input,
            ctx,
            "scope:" + step["step_id"],
            "clarification",
            {"question": step["proposal"]["question"], "scope_type": kind, "options": options},
        )
        # Always interrupt, even if the answer is already in SQL. LangGraph needs
        # Command(resume=...) to match/replay this exact saved interrupt.
        decision = interrupt(
            {k: request[k] for k in ("interrupt_id", "type", "expected_version", "payload")}
        )
        await asyncio.to_thread(store.close_input, ctx, request["interrupt_id"], decision)
        scope = dict(state["scope"])
        choice = decision["choice"]
        if choice not in options:
            raise Conflict("恢复答复不属于原选项")
        if kind == "sku":
            scope["skus"] = [choice]
        elif kind == "category":
            scope["skus"] = [
                p["sku"]
                for p in data["sales_payload"]["products"]
                if p["sku"] in scope["skus"] and p["category"] == choice
            ]
        else:
            scope["objective"] = choice
        step = await update(
            sequence,
            status="SUCCEEDED",
            assessment={"next_action": "propose", "scope_narrowed": True, "scope": scope},
        )
        return {"scope": scope, "current_step": step, "no_information_count": 0}

    async def assemble(state):
        data = await snapshot()
        findings = findings_fn(ctx.task_id, data["evidence"], data["steps"])
        await asyncio.to_thread(store.save_findings, ctx, findings)
        missing = (
            gaps_fn(task["query"], data["evidence"], state["scope"], data["steps"])
            if v15
            else gaps_fn(task["query"], data["evidence"], state["scope"])
        )
        questions = list(dict.fromkeys(state.get("open_questions", []) + missing))
        try:
            report = make_report(
                data["task"],
                data["bundle"],
                data["evidence"],
                data["steps"],
                state["scope"],
                state.get("stop_reason", "exploration_complete"),
                questions,
                decision_mode,
            )
        except ValidationError:
            raise ReportValidationFailed() from None
        saved = await asyncio.to_thread(store.save_report, ctx, report)
        return {
            **saved,
            "report_version": saved["report"]["report_version"],
            "open_questions": questions,
            "steps": data["steps"],
            "finding_refs": [f["finding_id"] for f in findings],
        }

    async def review(state):
        if not task["parameters"]["require_review"]:
            return {"approved": True}
        request = await asyncio.to_thread(
            store.ensure_input,
            ctx,
            "report:" + state["report_hash"],
            "report_review",
            {"report_version": state["report_version"], "report_hash": state["report_hash"]},
        )
        decision = interrupt(
            {k: request[k] for k in ("interrupt_id", "type", "expected_version", "payload")}
        )
        await asyncio.to_thread(store.close_input, ctx, request["interrupt_id"], decision)
        if (
            decision.get("report_hash") != state["report_hash"]
            or type(decision.get("approved")) is not bool
        ):
            raise Conflict("复核与当前报告不一致")
        return {
            "approved": decision["approved"],
            "status": "ready" if decision["approved"] else "rejected",
        }

    async def export(state):
        if not state["validation"]["passed"]:
            raise ReportValidationFailed()
        evidence = (await snapshot())["evidence"]
        version, hashed = state["report_version"], state["report_hash"]
        if await asyncio.to_thread(store.published, ctx, version, hashed):
            return {
                "status": "degraded"
                if state["report"]["limitations"]
                or state["open_questions"]
                or state["stop_reason"] != "target_covered"
                else "succeeded"
            }
        relative = f"{ctx.task_id}/v{version}/{hashed}/{ctx.lease_token}"
        output = safe_path(settings.data_dir / "artifacts", relative)
        output.mkdir(parents=True, exist_ok=True)
        bodies = {
            f"report-v{version}.md": markdown(state["report"], hashed),
            f"report-v{version}.json": json.dumps(
                {"report": state["report"], "validation": state["validation"]},
                ensure_ascii=False,
                indent=2,
            ),
            f"evidence-v{version}.json": json.dumps(evidence, ensure_ascii=False, indent=2),
            f"diagnosis-trace-v{version}.json": json.dumps(
                {
                    "steps": state["steps"],
                    "stop_reason": state["stop_reason"],
                    "finding_refs": state["finding_refs"],
                },
                ensure_ascii=False,
                indent=2,
            ),
        }
        await asyncio.to_thread(
            repo.emit,
            ctx,
            "diagnosis_exporting",
            "正在生成报告版本产物",
            {"report_version": version},
        )
        for name, content in bodies.items():
            await asyncio.to_thread((output / name).write_text, content, encoding="utf-8")
        result = await asyncio.to_thread(
            convert_md_to_pdf, output / f"report-v{version}.md", output / f"report-v{version}.pdf"
        )
        if not result.startswith("成功转换:"):
            raise RuntimeError("pdf_export_failed")
        files = {
            p.name: {"storage_key": f"{relative}/{p.name}", "content": p.read_bytes()}
            for p in output.iterdir()
            if p.is_file()
        }
        await asyncio.to_thread(store.publish_artifacts, ctx, version, hashed, files)
        degraded = bool(
            state["open_questions"]
            or state["stop_reason"] != "target_covered"
            or state["report"]["limitations"]
        )
        return {"status": "degraded" if degraded else "succeeded"}

    def after_propose(state):
        if state.get("stop_reason"):
            return "assemble"
        step = state["current_step"]
        if step["proposal"]["action"] == "stop":
            return "authorize"
        if step["status"] in STEP_FINAL or step["proposal"]["action"] == "invalid":
            return "assess"
        return "authorize"

    def after_authorize(state):
        if state.get("stop_reason"):
            return "assemble"
        step = state["current_step"]
        if step["status"] == "REJECTED":
            return "assess"
        return "clarify" if step["proposal"]["action"] == "clarify" else "execute_tool"

    builder = StateGraph(DiagnosisState)
    for name, node in (
        ("resolve_inputs", resolve_inputs),
        ("baseline_inspect", baseline_inspect),
        ("baseline_compare", baseline_compare),
        ("propose", propose),
        ("authorize", authorize),
        ("execute_tool", execute_tool),
        ("assess", assess),
        ("clarify", clarify),
        ("assemble", assemble),
        ("review", review),
        ("export", export),
    ):
        builder.add_node(name, node)
    builder.add_edge(START, "resolve_inputs")
    builder.add_edge("resolve_inputs", "baseline_inspect")
    builder.add_conditional_edges(
        "baseline_inspect", lambda s: "assemble" if s.get("stop_reason") else "baseline_compare"
    )
    builder.add_conditional_edges(
        "baseline_compare", lambda s: "assemble" if s.get("stop_reason") else "propose"
    )
    builder.add_conditional_edges("propose", after_propose)
    builder.add_conditional_edges("authorize", after_authorize)
    builder.add_edge("execute_tool", "assess")
    builder.add_conditional_edges(
        "assess", lambda s: "assemble" if s.get("stop_reason") else "propose"
    )
    builder.add_conditional_edges(
        "clarify", lambda s: "assess" if s["current_step"]["status"] == "REJECTED" else "propose"
    )
    builder.add_edge("assemble", "review")
    builder.add_conditional_edges("review", lambda s: "export" if s["approved"] else END)
    builder.add_edge("export", END)
    return builder.compile(checkpointer=checkpointer)
