"""Versioned diagnostic report; all facts and verifiable observations are rendered by code."""

import json

from pydantic import ValidationError

from app.diagnosis.contracts import is_extended
from app.diagnosis.inputs import digest
from app.diagnosis.planner import initial_scope, missing_coverage
from app.diagnosis.schemas import DiagnosticReport
from app.diagnosis.validation import observations, selected_fact_keys

STOP_TEXT = {
    "target_covered": "预定义核查证据已覆盖；仅形成观察，不宣称证明经营因果",
    "exploration_complete": "本轮探索结束，开放问题不宣称已完整解决",
    "evidence_gap": "现有固定输入不足，需要补充资料并创建新任务",
    "no_information": "连续步骤未产生新的可用信息",
    "step_budget": "达到诊断步骤上限",
    "tool_budget": "业务工具调用额度不足",
    "model_budget": "模型调用额度不足，保留报告阶段额度后提前收束",
    "usage_unknown": "模型用量或结果未知，停止进一步模型请求",
    "invalid_decision": "连续提议未通过校验，停止无效修正",
    "no_evidence": "没有可核对的有效事实",
}


def fact_text(value):
    unit = {"CNY": "元", "count": "件", "percent": "%", "days": "天", "units_per_day": "件/天"}[
        value["unit"]
    ]
    p = value["period"]
    return f"{value['label']}：{value['value']}{unit}（对象 {value['entity_id']}，{p['start']} 至 {p['end']}）"


def bound_fact(source, key, extended=False):
    value = source["payload"]["fact_catalog"][key]
    return {
        "evidence_id": source["id"],
        "metric_key": key,
        **{
            k: value[k]
            for k in ("value", "unit", "entity_type", "entity_id", "period", "comparison_period")
        },
        "text": fact_text(value)
        + (
            f"；固定快照日期 {value['snapshot_as_of']}"
            if extended and value.get("snapshot_as_of")
            else ""
        ),
        **({"snapshot_as_of": value.get("snapshot_as_of")} if extended else {}),
    }


def report_refs(task, evidence, scope):
    keys = report_contract(task)[0]
    refs = [(e["id"], key) for e in evidence for key in keys(e["payload"])]
    if task["graph_version"] in {"diagnosis-v11", "diagnosis-v12", "diagnosis-v13"}:
        from app.diagnosis.planner_v11 import component_obligations

        if task["graph_version"] == "diagnosis-v13":
            from app.diagnosis.planner_v13 import component_obligations

        required = {
            i["fact_key"]
            for i in component_obligations(task["query"], evidence, scope)
            if i["status"] == "acquired"
        }
        refs.sort(key=lambda ref: ref[1] not in required)
    return refs[:250]


def display_gaps(task, evidence, scope):
    if task["graph_version"] not in {"diagnosis-v11", "diagnosis-v12", "diagnosis-v13"}:
        return []
    from app.diagnosis.planner_v11 import component_obligations

    if task["graph_version"] == "diagnosis-v13":
        from app.diagnosis.planner_v13 import component_obligations

    selected = set(report_refs(task, evidence, scope))
    shown = {
        (f["entity_id"], key)
        for e in evidence
        for key, f in e["payload"].get("fact_catalog", {}).items()
        if (e["id"], key) in selected
    }
    omitted = [
        f"{i['sku']}/{i['metric']}/{i['part']}"
        for i in component_obligations(task["query"], evidence, scope)
        if i["status"] == "acquired" and (i["sku"], i["fact_key"]) not in shown
    ]
    return ["报告展示容量不足，未展示必答项：" + "、".join(omitted)] if omitted else []


def make_report(task, bundle, evidence, steps, scope, stop_reason, open_questions, execution_mode):
    keys, finding_fn, schema, _ = report_contract(task)
    facts = [bound_fact(e, key, is_extended(task)) for e in evidence for key in keys(e["payload"])]
    limitations = list(
        dict.fromkeys(
            [s for e in evidence for s in e["payload"].get("limitations", [])]
            + [
                "没有成本、曝光、访客和订单关联，不能计算利润、转化率、广告ROI或同批订单最终退款率。",
                "候选支持状态只对应确定性观察规则，不能当作因果证明或统计置信度。",
                "日序列报告展示首尾及极值代表日期，完整明细在授权证据中。",
                "每条事实以其实体范围为准；范围缩小前的基线保留初始授权范围。",
            ]
        )
    )
    if task["graph_version"] in {"diagnosis-v11", "diagnosis-v12", "diagnosis-v13"}:
        limitations.append(
            "启用前日期不适用、不要求补零；启用后零销量须有真实显式零记录，缺行不能造零。"
        )
    if len(facts) > 250:
        limitations.append("报告事实展示已截断，完整事实目录保留在证据中。")
    sources = {e["id"]: e for e in evidence}
    facts = [
        bound_fact(sources[eid], key, is_extended(task))
        for eid, key in report_refs(task, evidence, scope)
    ]
    omitted = display_gaps(task, evidence, scope)
    if omitted:
        open_questions = [*omitted, *open_questions]
        if stop_reason == "target_covered":
            stop_reason = "evidence_gap"
    full_questions = list(dict.fromkeys(open_questions))
    if task["graph_version"] == "diagnosis-v13" and len(full_questions) > 20:
        limitations.insert(
            0,
            f"未完成问题共{len(full_questions)}项，正文展示20项，省略{len(full_questions) - 20}项；完整清单见同版本JSON报告及工作台完整清单。",
        )
    report = {
        "profile": "commerce_diagnosis",
        "schema_version": 3
        if task["graph_version"] == "diagnosis-v13"
        else 2
        if is_extended(task)
        else 1,
        "title": "经营诊断报告",
        "objective": task["query"],
        "bundle_id": bundle["bundle_id"],
        "bundle_hash": bundle["bundle_hash"],
        "scope": scope,
        "period": bundle["period"],
        "comparison_period": bundle["comparison_period"],
        "versions": {
            k: task[k]
            for k in ("graph_version", "schema_version", "prompt_version", "tool_registry_version")
        },
        "report_version": max(1, task["report_version"]),
        "execution_mode": execution_mode,
        "facts": facts,
        "observations": finding_fn(task["id"], evidence, steps),
        "steps": steps,
        "stop_reason": stop_reason,
        "open_questions": list(dict.fromkeys(open_questions))[:20],
        "recommendations": [
            "按证据中的商品、品类和具体日期核对原始经营台账。",
            "若需解释业务原因，补充退款原因、渠道、流量或订单关联资料后创建新的输入版本和任务。",
            "库存、规则和业务操作均须在相应数据与人工流程下另行核对，不据本报告自动执行改价或采购。",
        ],
        "limitations": limitations[:30],
        "usage": {k: v for k, v in task["metrics"].items() if k != "runtime_ms"},
    }
    if task["graph_version"] == "diagnosis-v13":
        from app.diagnosis.planner_v13 import component_obligations

        report.update(
            request_contract=task["parameters"]["answer_contract"],
            request_obligations=component_obligations(task["query"], evidence, scope),
            open_question_details=full_questions,
            gap_summary={
                "total": len(full_questions),
                "displayed": min(20, len(full_questions)),
                "omitted": max(0, len(full_questions) - 20),
            },
        )
    if is_extended(task):
        from app.diagnosis.validation_v2 import interpretations, rule_conflicts, rule_quotes

        report.update(
            rule_quotes=rule_quotes(evidence),
            rule_interpretations=interpretations(steps, evidence),
            rule_conflicts=rule_conflicts(evidence),
        )
        report["recommendations"][-1] = (
            "库存代理须由实际出库、在途及多仓资料核对；规则原文和未核验解读须由人工确认适用范围与版本。"
        )
        report["limitations"].append(
            "规则引用只经过逐字重算，解读与冲突候选均未核验；复核导出不改变此状态。"
        )
        if sum(len(e["payload"].get("items", [])) for e in evidence) > 40:
            report["limitations"].append("观察展示受50条上限约束，完整逐SKU状态保留在证据中。")
    return schema.model_validate(report).model_dump(mode="json")


def report_contract(task):
    if is_extended(task):
        from app.diagnosis.planner_v2 import missing_coverage as gaps
        from app.diagnosis.schemas_v2 import DiagnosticReportV2
        from app.diagnosis.validation_v2 import observations as findings
        from app.diagnosis.validation_v2 import selected_fact_keys as keys

        if task["graph_version"] in {"diagnosis-v3", "diagnosis-v4"}:
            from app.diagnosis.planner_v3 import missing_coverage as gaps
        if task["graph_version"] in {"diagnosis-v5", "diagnosis-v6"}:
            from app.diagnosis.planner_v5 import missing_coverage as gaps
        if task["graph_version"] in {"diagnosis-v7", "diagnosis-v8"}:
            from app.diagnosis.planner_v7 import missing_coverage as gaps
        if task["graph_version"] == "diagnosis-v13":
            from app.diagnosis.planner_v13 import missing_coverage as gaps
            from app.diagnosis.schemas_v3 import DiagnosticReportV3

            return keys, findings, DiagnosticReportV3, gaps
        if task["graph_version"] == "diagnosis-v12":
            from app.diagnosis.planner_v12 import missing_coverage as gaps
        elif task["graph_version"] == "diagnosis-v11":
            from app.diagnosis.planner_v11 import missing_coverage as gaps
        elif task["graph_version"] == "diagnosis-v10":
            from app.diagnosis.planner_v10 import missing_coverage as gaps
        elif task["graph_version"] == "diagnosis-v9":
            from app.diagnosis.planner_v9 import missing_coverage as gaps

        return keys, findings, DiagnosticReportV2, gaps
    return selected_fact_keys, observations, DiagnosticReport, missing_coverage


def validate_report(report, task, bundle, evidence, steps):
    errors = []
    keys, finding_fn, schema, gap_fn = report_contract(task)
    try:
        report = schema.model_validate(report).model_dump(mode="json")
    except ValidationError:
        return {"passed": False, "errors": ["invalid_report_schema"]}
    sources = {e["id"]: e for e in evidence}
    expected_scope = initial_scope(task["query"], bundle)
    if task["graph_version"] == "diagnosis-v13":
        from app.commerce.answer_contract_v2 import bound_request
        from app.commerce.answer_contract_v2 import initial_scope as v13_scope

        expected_scope = v13_scope(task["query"], bundle)
        if task["parameters"].get("answer_contract") != bound_request(task["query"], bundle):
            errors.append("request_contract_changed")
    for step in steps:
        if step["proposal"]["action"] == "clarify" and step["status"] == "SUCCEEDED":
            expected_scope = (step["assessment"] or {}).get("scope", expected_scope)
    if report["scope"] != expected_scope:
        errors.append("scope_decision_mismatch")
    if report["bundle_id"] != bundle["bundle_id"] or report["bundle_hash"] != bundle["bundle_hash"]:
        errors.append("wrong_input_version")
    if (
        report["period"] != bundle["period"]
        or report["comparison_period"] != bundle["comparison_period"]
    ):
        errors.append("wrong_period")
    if report["versions"] != {
        k: task[k]
        for k in ("graph_version", "schema_version", "prompt_version", "tool_registry_version")
    }:
        errors.append("wrong_implementation_version")
    if not set(report["scope"].get("skus", [])) <= set(bundle["allowed_skus"]):
        errors.append("scope_not_authorized")
    for fact in report["facts"]:
        source = sources.get(fact["evidence_id"])
        if not source or fact["metric_key"] not in source["payload"].get("fact_catalog", {}):
            errors.append("unknown_fact_reference")
        elif fact != bound_fact(source, fact["metric_key"], is_extended(task)):
            errors.append("fact_or_unit_mismatch")
    expected_refs = report_refs(task, evidence, report["scope"])
    if [(f["evidence_id"], f["metric_key"]) for f in report["facts"]] != expected_refs:
        errors.append("required_fact_selection_changed")
    if report["observations"] != finding_fn(task["id"], evidence, steps):
        errors.append("observation_not_program_verified")
    if task["graph_version"] in {
        "diagnosis-v10",
        "diagnosis-v11",
        "diagnosis-v12",
        "diagnosis-v13",
    }:
        from app.diagnosis.planner_v10 import public_proposal

        if any(s["proposal"] != public_proposal(s["proposal"]) for s in report["steps"]):
            errors.append("unverified_step_description")
    if report["steps"] != steps:
        errors.append("step_ledger_mismatch")
    if report["stop_reason"] not in STOP_TEXT:
        errors.append("unknown_stop_reason")
    if report["objective"] != task["query"] or report["title"] != "经营诊断报告":
        errors.append("report_objective_changed")
    gaps = set(gap_fn(task["query"], evidence, report["scope"])) | {
        "本期没有可核对的已记录事实",
        "两期数据覆盖不足，需补充原始数据并新建任务",
        "所问指标或操作超出当前数据与只读边界",
        "调用结果或用量未知，未继续发起模型请求",
    }
    if task["graph_version"] == "diagnosis-v11":
        gaps = set(gap_fn(task["query"], evidence, report["scope"])) | set(
            display_gaps(task, evidence, report["scope"])
        )
        for text in ("本期没有可核对的已记录事实", "两期数据覆盖不足，需补充原始数据并新建任务"):
            if text in [g for e in evidence for g in e["payload"].get("limitations", [])] or any(
                e["payload"].get("comparable") is False for e in evidence
            ):
                gaps.add(text)
        if task["metrics"].get("usage_complete") is False:
            gaps.add("调用结果或用量未知，未继续发起模型请求")
        if report["stop_reason"] == "target_covered" and gaps:
            errors.append("unfulfilled_request_items")
    if task["graph_version"] in {"diagnosis-v12", "diagnosis-v13"}:
        gaps = set(gap_fn(task["query"], evidence, report["scope"])) | set(
            display_gaps(task, evidence, report["scope"])
        )
        if task["metrics"].get("usage_complete") is False:
            gaps.add("调用结果或用量未知，未继续发起模型请求")
        if report["stop_reason"] == "target_covered" and gaps:
            errors.append("unfulfilled_request_items")
    checked_questions = report.get("open_question_details", report["open_questions"])
    if task["graph_version"] == "diagnosis-v13" and not set(
        gap_fn(task["query"], evidence, report["scope"])
    ) | set(display_gaps(task, evidence, report["scope"])) <= set(checked_questions):
        errors.append("missing_required_gap")
    if set(checked_questions) - gaps:
        errors.append("unverified_gap_statement")
    template = make_report(
        task,
        bundle,
        evidence,
        steps,
        report["scope"],
        report["stop_reason"],
        checked_questions,
        report["execution_mode"],
    )
    for name in (
        "recommendations",
        "limitations",
        "usage",
        "report_version",
        *(
            (
                "request_contract",
                "request_obligations",
                "open_question_details",
                "gap_summary",
                "open_questions",
            )
            if task["graph_version"] == "diagnosis-v13"
            else ()
        ),
        *(("rule_quotes", "rule_interpretations", "rule_conflicts") if is_extended(task) else ()),
    ):
        if report[name] != template[name]:
            errors.append("unverified_" + name)
    if task["mode"] == "demo" and report["execution_mode"] == "live_model":
        errors.append("demo_mislabeled_as_real_model")
    extension_fact = is_extended(task) and (
        report.get("rule_quotes")
        or any(
            e["payload"].get("fact_catalog") and e["payload"]["tool"] == "inventory_risk"
            for e in evidence
        )
    )
    if not extension_fact and not any(
        e["payload"].get("coverage", {}).get("current", {}).get("observed_records", 0)
        for e in evidence
    ):
        errors.append("no_recorded_facts")
    return {
        "passed": not errors,
        "errors": sorted(set(errors)),
        "semantic_status": "program_verified",
        "report_hash": digest(report),
        "numeric_truth": "recomputed_from_bound_source",
        **(
            {"rule_semantics": "unverified", "rule_quotes": "recomputed_literal_match"}
            if is_extended(task)
            else {}
        ),
    }


def markdown(report, report_hash):
    # Data is plain text in the export, never an executable HTML/link instruction.
    def plain(value):
        return (
            str(value).replace("\n", " ").replace("|", "／").replace("<", "＜").replace(">", "＞")
        )

    text = [
        "# 经营诊断报告",
        "",
        "## 任务范围与数据版本",
        "",
        plain(report["objective"]),
        "",
        f"输入包：{report['bundle_id']}；哈希：{report['bundle_hash']}",
        f"报告版本：{report['report_version']}；哈希：{report_hash}",
        f"本期：{report['period']['start']} 至 {report['period']['end']}",
        f"对比期：{report['comparison_period']['start']} 至 {report['comparison_period']['end']}",
        f"执行方式：{report['execution_mode']}",
        "",
        "## 经营事实及可比性",
        "",
    ]
    text.extend(
        f"- {plain(f['text'])} [{f['evidence_id']} / {f['metric_key']}]" for f in report["facts"]
    )
    text.extend(["", "## 诊断过程", ""])
    text.extend(
        f"- 步骤{s['sequence']}：拟核对 {plain(s['proposal']['question'])}；{s['status']}"
        for s in report["steps"]
    )
    text.extend(["", "停止原因：" + STOP_TEXT[report["stop_reason"]], "", "## 观察与候选解释", ""])
    text.extend(
        f"- {f['status']}（对象 {plain(','.join(f.get('scope', {}).get('skus', [])))}）：{plain(f['text'])}（{f['finding_id']}）"
        for f in report["observations"]
    )
    if report.get("schema_version") in {2, 3}:
        text.extend(["", "### 规则原文与未核验解读", ""])
        text.extend(
            f"- 原文（解读未核验）：{plain(q['quote'])} [{q['evidence_id']} / {plain(q['chunk_id'])} / {q['quote_hash']}]"
            for q in report["rule_quotes"]
        )
        text.extend(
            f"- 未核验解读：{plain(item['text'])}；引用：{plain(json.dumps(item['references'], ensure_ascii=False))}"
            for item in report["rule_interpretations"]
        )
        text.extend(
            f"- 未核验冲突候选：{plain(item['reason'])} [{item['evidence_id']}]"
            for item in report["rule_conflicts"]
        )
    text.extend(["", "## 核对建议与未完成问题", ""])
    text.extend("- " + plain(s) for s in report["recommendations"] + report["open_questions"])
    if report.get("schema_version") == 3 and report["gap_summary"]["omitted"]:
        text.append("完整未完成清单包含于同版本JSON报告，可从工作台下载查看。")
    text.extend(["", "## 限制、用量与版本", ""])
    text.extend("- " + plain(s) for s in report["limitations"])
    text.extend(
        [
            "",
            "用量：" + json.dumps(report["usage"], ensure_ascii=False),
            "版本：" + json.dumps(report["versions"], ensure_ascii=False),
            "复核仅确认具体报告版本的导出，不授予业务写权限。",
            "",
        ]
    )
    return "\n".join(text)
