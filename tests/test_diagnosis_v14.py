"""Pre-provider independent counterexamples, old contracts and durable step replay."""

import asyncio
import contextlib
import copy

import pytest

from app.commerce.answer_contract_v3 import entity_selection, request_contract
from app.diagnosis.model_calls import DiagnosisModelGateway
from app.tasks.worker import Worker, checkpoint_store
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_runtime import admit, collect, execute, new_task, sandbox
from tests.test_diagnosis_graph import tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel
from tests.test_diagnosis_v3 import stop

CASES = {c.case_id: c for c in load_cases()[0] if c.split == "development"}


@pytest.mark.parametrize(
    "query,wanted",
    [
        ("商品 A 除外，比较其余商品的两期净销售额及差额", ["B", "C"]),
        ("不要商品A和B，只看商品C的两期净销售额及差额", ["C"]),
        ("除商品A、商品B之外，比较剩余商品", ["C"]),
        ("不计算利润，比较商品A的两期净销售额", ["A"]),
        ("只核对饮具的两期净额", ["A"]),
        ("饮具除外，比较其余商品两期净额", ["B", "C"]),
    ],
)
def test_literal_entity_polarity_and_category_catalog(query, wanted):
    catalog = [
        {"sku": "A", "category": "饮具"},
        {"sku": "B", "category": "收纳"},
        {"sku": "C", "category": "收纳"},
    ]
    assert entity_selection(query, ["A", "B", "C"], catalog)["selected"] == wanted


def test_identifier_named_sku01_is_not_misread_as_unknown_01():
    assert entity_selection("比较SKU01的两期净额", ["SKU01", "SKU02"])["selected"] == ["SKU01"]


@pytest.mark.parametrize(
    "query", ["只看商品A和FOREIGN", "不要不看商品A", "排除A和B", "只看商品A，商品A除外"]
)
def test_unsafe_entity_requests_require_explicit_restatement(query):
    with pytest.raises(ValueError):
        entity_selection(query, ["A", "B"])


@pytest.mark.parametrize(
    "query,wanted",
    [
        ("不要按品类核对贡献，只按商品核对两期净销售额变化贡献并对账", ["sku"]),
        ("不看商品贡献，按品类核对两期净销售额贡献", ["category"]),
        ("找出净销售额下降最多的两个品类并核对两期金额", ["category"]),
        ("分别按商品和品类核对两期净销售额贡献", ["category", "sku"]),
    ],
)
def test_dimension_polarity_and_implicit_grouping(query, wanted):
    assert request_contract(query)["contribution_dimensions"] == wanted


def test_metric_periods_are_bound_per_clause_and_exclusions_are_preserved():
    c = request_contract("逐商品列出本期支付金额，比较期退款金额；不要展示净销售额")
    assert c["metric_periods"] == {"gross_revenue": ["current"], "refund_amount": ["comparison"]}
    assert c["excluded_metrics"] == ["net_revenue"]


@pytest.mark.parametrize("runtime,wanted", [("v13", ["A"]), ("v14", ["B"])])
async def test_postfix_exclusion_worker_counterfactual(runtime, wanted):
    case = CASES["D01"].model_copy(
        update={"query": "商品 A 除外，比较其余商品的两期净销售额及差额。"}
    )
    with sandbox(diagnosis_runtime=runtime) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel()
        await execute(repo, settings, model=model)
        run = collect(repo, task["id"], "agent")
        assert run["task"]["validation"]["passed"], run["task"]
        assert run["task"]["report"]["scope"]["skus"] == wanted
        assert run["task"]["report"]["stop_reason"] == "target_covered"
        assert not model.requests


@pytest.mark.parametrize(
    "query,wrong,right",
    [
        ("不要按品类核对贡献，只按商品核对两期净销售额变化贡献并对账。", "category", "sku"),
        ("找出净销售额下降最多的两个品类并核对两期金额。", "sku", "category"),
    ],
)
async def test_wrong_dimension_can_explore_but_cannot_complete(query, wrong, right):
    case = CASES["D13"].model_copy(update={"query": query})
    with sandbox(diagnosis_runtime="v14", max_model_calls=3) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel(
            tool_proposal("rank_contributors", {"dimension": wrong}),
            stop(),
            tool_proposal("rank_contributors", {"dimension": right}),
        )
        await execute(repo, settings, model=model)
        run = collect(repo, task["id"], "agent")
        assert run["task"]["validation"]["passed"], run["task"]
        assert run["steps"][1]["status"] == "REJECTED"
        assert run["task"]["report"]["stop_reason"] == "target_covered"


async def saved_response_recovery(setup, runtime, limit):
    settings, repo, user, other = setup
    settings.diagnosis_runtime = "legacy" if runtime in {"v1", "v2"} else runtime
    settings.max_model_calls = limit
    case = CASES["D01"]
    if runtime == "v2":
        case = case.model_copy(update={"query": case.query + "；规则原文"})
    conv, bundle, _ = admit(repo, user, other, case)
    task = new_task(repo, user, conv, bundle, case.query, "agent")
    model = ScriptedDecisionModel(
        tool_proposal("rank_contributors", {}), tool_proposal("analyze_refunds", {})
    )
    original, durable, hold = DiagnosisModelGateway.invoke, asyncio.Event(), asyncio.Event()

    async def stop_after_response(*args, **kwargs):
        result = await original(*args, **kwargs)
        durable.set()
        await hold.wait()
        return result

    async with checkpoint_store(settings) as saver:
        first = Worker(repo, settings, saver, model=model)
        DiagnosisModelGateway.invoke = stop_after_response
        execution = asyncio.create_task(first.run_once())
        try:
            await asyncio.wait_for(durable.wait(), 10)
            first.stopping.set()
            execution.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await execution
        finally:
            DiagnosisModelGateway.invoke = original
        before = collect(repo, task["id"], "agent")
        assert before["model_invocations"][0]["status"] == "SUCCEEDED"
        assert before["steps"] == []
        await Worker(repo, settings, saver, model=model).run_once()
    after = collect(repo, task["id"], "agent")
    assert after["task"]["validation"]["passed"], after["task"]
    assert after["steps"][0]["status"] == "SUCCEEDED"
    assert after["model_invocations"][0] == before["model_invocations"][0]
    assert len(model.requests) == (1 if limit == 1 else 2)
    assert after["task"]["report"]["stop_reason"] == (
        "model_budget" if limit == 1 else "target_covered"
    )


@pytest.mark.parametrize("runtime", ["v13", "v14"])
@pytest.mark.parametrize("limit", [1, 12])
async def test_saved_model_response_is_recovered_before_budget_or_prompt_rebuild(runtime, limit):
    with sandbox(diagnosis_runtime=runtime, max_model_calls=limit) as setup:
        await saved_response_recovery(setup, runtime, limit)


async def test_known_category_selection_uses_authorized_source_catalog():
    case = CASES["D01"].model_copy(update={"query": "只比较饮具的两期净销售额及差额。"})
    with sandbox(diagnosis_runtime="v14") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        await execute(repo, settings, model=ScriptedDecisionModel())
        run = collect(repo, task["id"], "agent")
        assert run["task"]["validation"]["passed"], run["task"]
        assert run["task"]["report"]["scope"]["skus"] == ["A"]


def test_grouping_axis_is_not_inferred_from_a_descriptive_product_word():
    from evaluations.diagnosis_request_checks_v2 import requested_dimensions

    query = "按品类核对所有商品的两期净销售额贡献"
    assert request_contract(query)["contribution_dimensions"] == ["category"]
    assert requested_dimensions(query) == ["category"]


def test_subtracting_refunds_is_not_a_request_to_hide_refunds():
    from evaluations.diagnosis_request_checks_v2 import excluded_metrics, requested_items

    query = "逐商品核对支付金额扣除退款金额的两期净销售额及差额"
    c = request_contract(query)
    assert c["excluded_metrics"] == []
    assert set(c["component_metrics"]) == {"net_revenue", "gross_revenue", "refund_amount"}
    assert excluded_metrics(query) == set()
    assert len(requested_items(query, ["A", "B"])) == 18


async def test_excluded_metrics_do_not_leak_into_report_facts():
    from app.diagnosis.reporting import validate_report

    query = "逐商品列出本期支付金额；不要展示净销售额，勿列退款金额"
    case = CASES["D01"].model_copy(update={"query": query})
    with sandbox(diagnosis_runtime="v14") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, query, "agent")
        await execute(
            repo, settings, model=ScriptedDecisionModel(tool_proposal("rank_contributors", {}))
        )
        run = collect(repo, task["id"], "agent")
        report = run["task"]["report"]
        assert run["task"]["validation"]["passed"], run["task"]
        assert report["stop_reason"] == "target_covered"
        assert not any(
            {"net_revenue", "refund_amount"}.intersection(f["metric_key"].split("."))
            for f in report["facts"]
        )
        bad = copy.deepcopy(report)
        source = next(
            e
            for e in run["evidence"]
            if "net_revenue.current" in e["payload"].get("fact_catalog", {})
        )
        from app.diagnosis.reporting import bound_fact

        bad["facts"].append(bound_fact(source, "net_revenue.current", True))
        assert not validate_report(bad, run["task"], bundle, run["evidence"], run["steps"])[
            "passed"
        ]


@pytest.mark.parametrize(
    "query,wrong",
    [
        ("不要按品类核对贡献，只按商品核对两期净销售额变化贡献并对账。", "category"),
        ("找出净销售额下降最多的两个品类并核对两期金额。", "sku"),
    ],
)
async def test_independent_grader_rejects_forged_completion_for_wrong_axis(query, wrong):
    from evaluations.diagnosis_grading import grade

    case = CASES["D13"].model_copy(update={"query": query})
    with sandbox(diagnosis_runtime="v14", max_model_calls=1) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, query, "agent")
        await execute(
            repo,
            settings,
            model=ScriptedDecisionModel(tool_proposal("rank_contributors", {"dimension": wrong})),
        )
        run = collect(repo, task["id"], "agent")
        assert run["task"]["validation"]["passed"]
        run["task"]["report"].update(
            stop_reason="target_covered",
            open_questions=[],
            open_question_details=[],
            gap_summary={"total": 0, "displayed": 0, "omitted": 0},
        )
        assert grade(case, bundle, run)["assertions"]["requested_dimension_bound"] is False


@pytest.mark.parametrize("window", ["before_send", "after_response"])
async def test_recovery_of_uncommitted_response_stays_unknown(setup, monkeypatch, window):
    from app.storage.repository import BudgetExceeded
    from tests.conftest import expire_lease
    from tests.test_diagnosis_model_calls import MESSAGES, Choice, model_gateway

    calls, tools, task, model = model_gateway(setup)

    def die(*args, **kwargs):
        raise SystemExit()

    monkeypatch.setattr(
        model if window == "before_send" else calls,
        "with_structured_output" if window == "before_send" else "_complete",
        die,
    )
    with pytest.raises(SystemExit):
        await calls.invoke(Choice, MESSAGES, step_id="step-1")
    expire_lease(tools.repo, task["id"])
    ctx = tools.repo.claim()
    tools.repo.account_unreported_usage(ctx)
    recovered = DiagnosisModelGateway(tools.repo, ctx, model, "script-v1")
    saved = await recovered.recover(Choice, step_id="step-1")
    assert saved["status"] == "UNKNOWN" and not saved["usage_complete"]
    with pytest.raises(BudgetExceeded):
        await recovered.invoke(Choice, MESSAGES, step_id="step-2")
    assert len(model.requests) == (0 if window == "before_send" else 1)


@pytest.mark.parametrize("deny", ["revoked", "cancelled"])
async def test_saved_output_recovery_reauthorizes_before_use(setup, deny):
    from app.storage.models import CommerceDataset
    from app.storage.repository import NotFound, TaskCancelled
    from tests.test_diagnosis_model_calls import MESSAGES, Choice, model_gateway

    calls, tools, task, model = model_gateway(setup, {"action": "rank"})
    await calls.invoke(Choice, MESSAGES, step_id="step-1")
    if deny == "revoked":
        with tools.repo.session() as db:
            from app.storage.models import AnalysisInputBundle

            bundle = db.get(AnalysisInputBundle, task["parameters"]["bundle_id"])
            db.get(CommerceDataset, bundle.sales_dataset_id).committed = False
            db.commit()
    else:
        tools.repo.cancel(task["id"], tools.ctx.user_id)
    with pytest.raises(NotFound if deny == "revoked" else TaskCancelled):
        await calls.recover(Choice, step_id="step-1")
    assert len(model.requests) == 1


async def test_v14_bound_contract_and_publishing_survive_default_change(setup):
    from uuid import uuid4

    from app.research.schemas import TaskRequest
    from app.storage.repository import Conflict

    settings, repo, user, other = setup
    settings.diagnosis_runtime = "v14"
    query = "商品 A 除外，比较其余商品的两期净销售额及差额。"
    conv, bundle, _ = admit(repo, user, other, CASES["D01"])
    request = TaskRequest(
        conversation_id=conv,
        query=query,
        profile="commerce_diagnosis",
        bundle_id=bundle["bundle_id"],
        sources=["sales"],
    )
    key = str(uuid4())
    task = repo.create_task(user, request, key)
    settings.diagnosis_runtime = "legacy"
    assert repo.create_task(user, request, key)["id"] == task["id"]
    await execute(repo, settings, model=ScriptedDecisionModel())
    run = collect(repo, task["id"], "agent")
    assert run["task"]["report"]["versions"]["graph_version"] == "diagnosis-v14"
    assert run["task"]["report"]["scope"]["skus"] == ["B"]
    assert len(repo.artifacts(task["id"])) == 5
    with pytest.raises(Conflict):
        repo.create_task(user, request.model_copy(update={"query": "只看商品A"}), key)


async def test_edited_saved_entity_catalog_is_rejected_before_any_model_or_tool():
    from app.storage.models import Task

    with sandbox(diagnosis_runtime="v14") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, CASES["D01"])
        task = new_task(repo, user, conv, bundle, CASES["D01"].query, "agent")
        with repo.session() as db:
            row = db.get(Task, task["id"])
            value = copy.deepcopy(row.parameters)
            value["answer_contract"]["entity_catalog"][0]["category"] = "篡改分类"
            row.parameters = value
            db.commit()
        model = ScriptedDecisionModel(tool_proposal("rank_contributors", {}))
        await execute(repo, settings, model=model)
        run = collect(repo, task["id"], "agent")
        assert run["task"]["error_code"] == "Conflict"
        assert not run["model_invocations"] and not run["tool_invocations"] and not model.requests


async def test_recover_rejects_changed_schema_and_regular_invoke_still_rejects_changed_prompt(
    setup,
):
    from pydantic import BaseModel

    from app.storage.repository import Conflict
    from tests.test_diagnosis_model_calls import MESSAGES, Choice, model_gateway

    class Different(BaseModel):
        value: int

    calls, _, _, model = model_gateway(setup, {"action": "rank"})
    await calls.invoke(Choice, MESSAGES, step_id="step-1")
    with pytest.raises(Conflict):
        await calls.recover(Different, step_id="step-1")
    with pytest.raises(Conflict):
        await calls.invoke(Choice, [("human", "changed")], step_id="step-1")
    assert len(model.requests) == 1


def test_category_words_do_not_capture_periods_or_identifier_substrings():
    products = [{"sku": "C1", "category": "本期"}, {"sku": "D", "category": "C"}]
    assert entity_selection("比较本期与前期净销售额", ["C1", "D"], products)["selected"] == [
        "C1",
        "D",
    ]
    assert entity_selection("比较商品C1的两期净额", ["C1", "D"], products)["selected"] == ["C1"]
    from evaluations.diagnosis_request_checks_v2 import expected_entities

    assert expected_entities("比较本期与前期净销售额", ["C1", "D"], products) == ["C1", "D"]
    assert expected_entities("比较商品C1的两期净额", ["C1", "D"], products) == ["C1"]


def test_english_negative_profit_does_not_hide_a_supported_followup():
    c = request_contract("Do not calculate profit; inspect refunds")
    assert c["unsupported_requests"] == [] and "profit" in c["excluded_metrics"]


def test_repetitive_untrusted_prefix_has_no_exponential_alternative_backtracking():
    c = request_contract("不要" + "已授权" * 250 + "X支付金额")
    assert c["requested_metrics"] == ["gross_revenue"]


def test_negated_sku_grouping_does_not_create_per_sku_presentation_obligations():
    from evaluations.diagnosis_request_checks_v2 import requested_items

    query = "不要按商品核对贡献，只按品类核对两期净销售额变化贡献并对账"
    c = request_contract(query)
    assert c["contribution_dimensions"] == ["category"]
    assert c["component_metrics"] == []
    assert requested_items(query, ["A", "B"]) == []


def test_per_category_delta_is_supported_but_uncontracted_absolute_matrix_requires_restatement():
    c = request_contract("核对各品类两期净销售额变化贡献并对账")
    assert c["contribution_dimensions"] == ["category"]
    with pytest.raises(ValueError, match="逐品类金额"):
        request_contract("逐品类列出本期支付金额、退款件数")


@pytest.mark.parametrize("bad_filters", [{"skus": ["A"]}, {"categories": ["饮具"]}])
async def test_model_cannot_reintroduce_excluded_entities_through_sku_or_category(bad_filters):
    query = "排除商品A，核对其余商品两期净销售额变化贡献"
    case = CASES["D01"].model_copy(update={"query": query})
    with sandbox(diagnosis_runtime="v14", max_model_calls=2) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, query, "agent")
        model = ScriptedDecisionModel(
            tool_proposal("rank_contributors", {"filters": bad_filters}),
            tool_proposal("rank_contributors", {}),
        )
        await execute(repo, settings, model=model)
        run = collect(repo, task["id"], "agent")
        assert run["task"]["validation"]["passed"], run["task"]
        assert run["task"]["report"]["scope"]["skus"] == ["B"]
        assert run["steps"][0]["status"] == "REJECTED"
        assert run["task"]["report"]["stop_reason"] == "target_covered"
        assert not any(f["entity_id"] == "A" for f in run["task"]["report"]["facts"])
        assert run["task"]["metrics"]["tool_calls"] == 3


def test_literal_quote_does_not_mask_an_explicit_calculation_in_the_same_clause():
    assert request_contract("逐字引用利润分配规则原文")["unsupported_requests"] == []
    assert request_contract("逐字引用规则原文并计算两期利润")["unsupported_requests"] == ["profit"]


@pytest.mark.parametrize(
    "runtime,expected_stop", [("v13", "evidence_gap"), ("v14", "evidence_gap")]
)
async def test_rule_quote_plus_positive_profit_preserves_an_explicit_gap(runtime, expected_stop):
    from evaluations.diagnosis_request_checks_v2 import completion_checks

    case = next(c for c in load_cases()[0] if c.case_id == "R19").model_copy(
        update={"query": "逐字引用补货规则原文并计算两期利润"}
    )
    with sandbox(diagnosis_runtime=runtime) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        await execute(
            repo,
            settings,
            model=ScriptedDecisionModel(
                tool_proposal("search_rule_documents", {"query": "补货规则"})
            ),
        )
        run = collect(repo, task["id"], "agent")
        report = run["task"]["report"]
        assert run["task"]["validation"]["passed"], run["task"]
        assert report["stop_reason"] == expected_stop
        if runtime == "v14":
            assert any("profit" in text for text in report["open_question_details"])
            assert completion_checks(
                case.model_dump(mode="json"),
                report,
                [e["payload"] for e in run["evidence"]],
                case.scope_skus,
            )["unsupported_positive_requests_preserved"]
            forged = copy.deepcopy(report)
            forged.update(
                stop_reason="target_covered",
                open_questions=[],
                open_question_details=[],
                gap_summary={"total": 0, "displayed": 0, "omitted": 0},
            )
            assert not completion_checks(
                case.model_dump(mode="json"),
                forged,
                [e["payload"] for e in run["evidence"]],
                case.scope_skus,
            )["unsupported_positive_requests_preserved"]


@pytest.mark.parametrize(
    "metric,label",
    [
        ("gross_revenue", "支付金额"),
        ("refund_amount", "退款金额"),
        ("paid_units", "支付件数"),
        ("refunded_units", "退款件数"),
    ],
)
@pytest.mark.parametrize(
    "period,parts",
    [
        ("本期", ["current"]),
        ("比较期", ["comparison"]),
        ("两期及差额", ["current", "comparison", "delta"]),
    ],
)
async def test_scope_metric_requires_exact_aggregate_and_requested_periods(
    metric, label, period, parts
):
    from evaluations.diagnosis_request_checks_v2 import completion_checks

    case = CASES["D01"].model_copy(update={"query": f"列出授权范围{period}的{label}"})
    with sandbox(diagnosis_runtime="v14", max_model_calls=3) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel(
            tool_proposal("compare_periods", {"metric": metric, "filters": {"skus": ["A"]}}),
            stop(),
            tool_proposal("compare_periods", {"metric": metric}),
        )
        await execute(repo, settings, model=model)
        run = collect(repo, task["id"], "agent")
        report = run["task"]["report"]
        assert run["task"]["validation"]["passed"], run["task"]
        assert run["steps"][1]["status"] == "REJECTED"
        assert report["stop_reason"] == "target_covered"
        for part in parts:
            assert any(
                f["entity_id"] == "A,B" and f["metric_key"] == f"{metric}.{part}"
                for f in report["facts"]
            )
        assert all(
            completion_checks(
                case.model_dump(mode="json"),
                report,
                [e["payload"] for e in run["evidence"]],
                case.scope_skus,
            ).values()
        )
        forged = copy.deepcopy(report)
        forged["facts"] = [
            f
            for f in forged["facts"]
            if f["entity_id"] != "A,B" or not f["metric_key"].startswith(metric + ".")
        ]
        assert not completion_checks(
            case.model_dump(mode="json"),
            forged,
            [e["payload"] for e in run["evidence"]],
            case.scope_skus,
        )["requested_scope_items"]


@pytest.mark.parametrize("part,word", [("current", "本期"), ("comparison", "前期")])
async def test_one_period_literal_does_not_require_or_display_absent_other_period(part, word):
    case = CASES["D01"]
    span = case.period if part == "current" else case.comparison_period
    sales = case.sales.model_copy(
        update={
            "daily_sales": [r for r in case.sales.daily_sales if span.start <= r.day <= span.end]
        }
    )
    case = case.model_copy(update={"query": f"列出{word}净销售额", "sales": sales})
    with sandbox(diagnosis_runtime="v14") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel()
        await execute(repo, settings, model=model)
        run = collect(repo, task["id"], "agent")
        report = run["task"]["report"]
        assert run["task"]["validation"]["passed"], run["task"]
        assert report["stop_reason"] == "target_covered"
        assert not model.requests
        assert report["facts"] and all(
            f["metric_key"] == f"net_revenue.{part}" for f in report["facts"]
        )


async def test_literal_deictic_entity_cannot_auto_complete_before_clarification():
    case = CASES["D01"].model_copy(update={"query": "列出那个商品的本期净销售额"})
    with sandbox(diagnosis_runtime="v14") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "demo")
        await execute(repo, settings)
        run = collect(repo, task["id"], "demo")
        assert run["task"]["status"] == "waiting_input", run["task"]
        assert run["task"]["report"] is None


def test_negative_metric_with_period_filler_stays_excluded():
    from evaluations.diagnosis_request_checks_v2 import excluded_metrics

    query = "不计算本期支付金额，列出本期净销售额"
    assert request_contract(query)["excluded_metrics"] == ["gross_revenue"]
    assert excluded_metrics(query) == {"gross_revenue"}


def test_followup_change_amount_expands_the_previous_metric_periods():
    from evaluations.diagnosis_request_checks_v2 import requested_items

    query = "前期净销售额非正时，哪些变化金额可算，哪些变化率不可算？"
    assert request_contract(query)["metric_periods"]["net_revenue"] == [
        "current",
        "comparison",
        "delta",
    ]
    assert set(requested_items(query, ["A", "B"], scope=True)) == {
        ("A,B", "net_revenue", part) for part in ["current", "comparison", "delta"]
    }


@pytest.mark.parametrize("runtime", [f"v{i}" for i in range(1, 13)])
async def test_legacy_saved_response_is_recovered_at_exhausted_model_budget(runtime):
    with sandbox(
        diagnosis_runtime="legacy" if runtime in {"v1", "v2"} else runtime, max_model_calls=1
    ) as setup:
        await saved_response_recovery(setup, runtime, 1)
