"""Programmatic observations and replayable evidence truth checks, never LLM voting."""

import copy
from decimal import Decimal

from app.commerce.service import money
from app.diagnosis.inputs import digest
from app.diagnosis.metrics import DiagnosticMetrics
from app.diagnosis.schemas import METRIC_VERSION, SALES_TOOLS, TOOL_VERSION, Filters
from app.diagnosis.tools import DiagnosisToolGateway


def information_digest(payload):
    facts = []
    for key, value in payload.get("fact_catalog", {}).items():
        if key == "contribution.other":
            continue  # A different Top-K remainder is presentation, not new source data.
        facts.append(
            {k: value[k] for k in ("entity_type", "entity_id", "unit", "raw_value", "period")}
        )
    return digest(
        {
            "scope": payload.get("scope"),
            "facts": sorted(facts, key=digest),
            "coverage": payload.get("coverage"),
            "products": payload.get("products"),
        }
    )


def verified_evidence(source, query, bundle, sales_payload, task_id, *, task=None, extras=None):
    from app.diagnosis.contracts import tool_schemas
    from app.diagnosis.tools import source_hashes

    p, spec = source["payload"], query["query_spec"]
    schemas = tool_schemas(task) if task else SALES_TOOLS
    version = task["tool_registry_version"] if task else TOOL_VERSION
    if (
        source["kind"] != "diagnosis_metrics"
        or p.get("bundle_id") != bundle["bundle_id"]
        or p.get("bundle_hash") != bundle["bundle_hash"]
        or p.get("metric_version") != METRIC_VERSION
        or p.get("tool_version") != version
        or p.get("evidence_ids") != [source["id"]]
        or p.get("source_hashes") != source_hashes(p.get("tool"), bundle)
        or digest(p) != source["content_hash"]
        or digest(spec) != p.get("query_spec_hash")
        or query["result_digest"] != digest(p)
        or query["result"] != p
        or spec.get("task_id") != task_id
        or spec.get("bundle_hash") != bundle["bundle_hash"]
        or spec.get("tool") != p.get("tool")
        or spec.get("tool") not in schemas
        or spec.get("tool_version") != version
        or spec.get("bundle_id") != bundle["bundle_id"]
        or spec.get("source_hashes") != p.get("source_hashes")
        or spec.get("metric_version") != METRIC_VERSION
        or spec.get("period") != bundle["period"]
        or spec.get("comparison_period") != bundle["comparison_period"]
        or (task and spec.get("sources") != sorted(task["parameters"]["sources"]))
    ):
        return False
    try:
        args = schemas[spec["tool"]].model_validate(spec["arguments"])
        calculation = DiagnosticMetrics(sales_payload, bundle, getattr(args, "filters", Filters()))
        expected = (
            DiagnosisToolGateway._calculate(
                spec["tool"], args, calculation, extras, registry_version=version
            )
            if version == "3"
            else DiagnosisToolGateway._calculate(spec["tool"], args, calculation, extras)
        )
    except (ValueError, KeyError, TypeError):
        return False
    actual = copy.deepcopy(p)
    for key in (
        "call_id",
        "bundle_id",
        "bundle_hash",
        "source_hashes",
        "tool",
        "tool_version",
        "query_spec_hash",
        "evidence_ids",
        "metric_version",
    ):
        actual.pop(key, None)
    for value in actual.get("fact_catalog", {}).values():
        if value.pop("evidence_id", None) != source["id"]:
            return False
        if value["unit"] == "CNY" and (
            type(value["raw_value"]) is not int or value["value"] != money(value["raw_value"])
        ):
            return False
    return actual == expected


def observations(task_id, evidence, steps=()):
    values = []

    def add(source, code, verdict, text, keys, missing=None):
        source_id = source["id"]
        status = (
            "insufficient"
            if verdict is None
            else "supported_observation"
            if verdict
            else "contradicted"
        )
        values.append(
            {
                "finding_id": "F-"
                + digest(
                    {"task": task_id, "code": code, "spec": source["payload"]["query_spec_hash"]}
                )[:24],
                "hypothesis_code": code,
                "status": status,
                "text": text,
                "scope": source["payload"].get("scope", {}),
                "supporting_evidence": [source_id] if verdict else [],
                "contradicting_evidence": [source_id] if verdict is False else [],
                "missing_data": missing
                or ([] if verdict is not None else ["当前证据不足以执行该项比较"]),
                "fact_refs": [{"evidence_id": source_id, "metric_key": key} for key in keys],
            }
        )

    for source in evidence:
        p = source["payload"]
        if p["tool"] == "inspect_dataset":
            missing = any(c["missing_records"] for c in p["coverage"].values())
            add(
                source,
                "data_coverage",
                missing,
                "至少一期存在启用后缺行，不能按完整数据作变化归因。"
                if missing
                else "两期适用记录均完整；启用前不要求补零。",
                [],
            )
        elif p["tool"] == "rank_contributors":
            rows = p["all_contributors"]
            valid = (
                None
                if not p["comparable"] or not any(r["delta_cents"] for r in rows)
                else bool(
                    rows
                    and abs(rows[0]["delta_cents"]) >= sum(abs(r["delta_cents"]) for r in rows[1:])
                )
            )
            add(
                source,
                "sales_concentration",
                valid,
                "单项绝对变化贡献不少于其余项目绝对变化的合计；这是记账贡献集中，不能证明经营因果。"
                if valid
                else "现有分解不支持单项绝对变化贡献达到其余项目绝对变化合计的判断。"
                if valid is False
                else "没有非零变化或覆盖不足，贡献集中程度不可判断。",
                [r["fact_key"] for r in p["contributors"]]
                + (["contribution.other", "contribution.total"] if rows else []),
            )
        elif p["tool"] == "analyze_refunds":
            delta = p["refund_amount_delta_cents"]
            valid = delta > 0 if delta is not None else None
            add(
                source,
                "refund_amount_change",
                valid,
                "退款金额增加，净销售额差额可按支付金额变化减退款金额变化核算；尚不能确定退款业务原因。"
                if valid
                else "退款金额未增加，不能用退款金额上升解释本期变化。"
                if valid is False
                else "两期不可比，退款金额变化不可判断。",
                [
                    k
                    for k in (
                        "refund_amount.current",
                        "refund_amount.comparison",
                        "refund_amount.delta",
                    )
                    if k in p["fact_catalog"]
                ],
            )
            valid = p["denominator_effect"] if p["ratio_increased"] is not None else None
            add(
                source,
                "denominator_effect",
                valid,
                "记账期退款件数占比增加，同时退款件数未增且支付件数减少，存在支付分母效应。"
                if valid
                else "当前证据不满足占比上升、退款件数未增且支付件数下降的分母效应条件。"
                if valid is False
                else "分母为零或覆盖不足，记账期占比变化不可判断。",
                [
                    k
                    for k in p["fact_catalog"]
                    if k.startswith(("paid_units.", "refunded_units.", "refund_ratio."))
                ],
            )
        elif p["tool"] == "drilldown_series":
            points = [r for r in p["series"] if r["period_name"] == "current"]
            complete = all(r["status"] not in {"missing", "not_active"} for r in points)
            amounts = [
                p["fact_catalog"][r["fact_keys"]["net_revenue"]]["raw_value"]
                for r in points
                if "net_revenue" in r["fact_keys"]
            ]
            verdict = max(amounts) != min(amounts) if complete and len(amounts) >= 2 else None
            add(
                source,
                "daily_distribution",
                verdict,
                "所选对象在本期不同日期的已记录净销售额不同；不能据此断言长期趋势或季节原因。"
                if verdict
                else "完整本期日序列的已记录净销售额相同。"
                if verdict is False
                else "不足两个完整适用日期，不能判断本期日分布变化。",
                [r["fact_keys"]["net_revenue"] for r in points if "net_revenue" in r["fact_keys"]][
                    :90
                ],
            )
    for step in steps:
        code = step["proposal"].get("hypothesis_code")
        if step["proposal"]["action"] != "tool":
            continue
        matching = [
            f
            for f in values
            if f["hypothesis_code"] == code
            and set(f["supporting_evidence"] + f["contradicting_evidence"])
            & set(step["evidence_ids"])
        ]
        if not matching:
            values.append(
                {
                    "finding_id": "F-"
                    + digest({"task": task_id, "step": step["step_id"], "code": code})[:24],
                    "hypothesis_code": code,
                    "status": "insufficient",
                    "text": "该步骤没有取得足以核验所选候选的对应事实。",
                    "supporting_evidence": [],
                    "contradicting_evidence": [],
                    "fact_refs": [],
                    "missing_data": ["所选工具、参数或数据不足以执行该候选的确定性规则"],
                    "step_id": step["step_id"],
                    "scope": {
                        "skus": (step.get("validated_args") or {})
                        .get("filters", {})
                        .get("skus", [])
                    },
                }
            )
    return values


def selected_fact_keys(payload):
    if payload["tool"] == "rank_contributors":
        keys = [
            key
            for r in payload["contributors"]
            for key in (r["fact_key"], r["share_fact_key"])
            if key
        ]
        return keys + (
            ["contribution.other", "contribution.total"] if payload["all_contributors"] else []
        )
    if payload["tool"] == "drilldown_series":
        chosen = []
        for name in ("current", "comparison"):
            points = [
                r
                for r in payload["series"]
                if r["period_name"] == name and "net_revenue" in r["fact_keys"]
            ]
            if points:

                def lookup(r):
                    return Decimal(payload["fact_catalog"][r["fact_keys"]["net_revenue"]]["value"])

                chosen.extend(
                    [points[0], min(points, key=lookup), max(points, key=lookup), points[-1]]
                )
        return list(dict.fromkeys(r["fact_keys"]["net_revenue"] for r in chosen))
    return list(payload.get("fact_catalog", {}))
