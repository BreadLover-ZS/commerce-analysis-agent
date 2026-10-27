"""Boundary semantics v2. Frozen predecessor remains in completion_v15.
Program-owned requirement states and typed amount witnesses for v15/review-v4.

Evidence adapters preserve original keys for citations. No model verdict, case label
or evaluation expectation participates in completion.
"""

from decimal import Decimal, InvalidOperation

from app.commerce.answer_contract_v5 import request_contract

PARTS = ("current", "comparison", "delta")
VALUE_METRICS = {"net_revenue", "gross_revenue", "refund_amount", "paid_units", "refunded_units"}
MONEY = ("net_revenue", "gross_revenue", "refund_amount")
TERMINAL_FAILURES = {
    "invalid_decision",
    "usage_unknown",
    "model_budget",
    "tool_budget",
    "step_budget",
    "no_information",
    "no_evidence",
}


def _fixed_key(key, payload):
    for part, prefix, suffix in [
        ("comparison", "comparison_", ""),
        ("delta", "", "_delta"),
        ("current", "", ""),
    ]:
        for metric in [*VALUE_METRICS, "refund_ratio"]:
            base = "refund_rate_pct" if metric == "refund_ratio" else metric
            if key == prefix + base + suffix:
                return metric, part
    for dim, entries in payload.get("dimensions", {}).items():
        for item in entries:
            for column, original in item.get("component_fact_keys", {}).items():
                if original == key:
                    return tuple(column.split("."))
    return None, None


def views(evidence, scope):
    facts = []
    for e in evidence:
        p = e["payload"]
        fixed = p.get("review_profile") == "commerce_review"
        if fixed:
            coverage = {
                "current": p.get("coverage", {}),
                "comparison": p.get("previous_period", {}).get("coverage", {}),
            }
        else:
            coverage = p.get("coverage", {})
        for key, f in p.get("fact_catalog", {}).items():
            metric, part = _fixed_key(key, p) if fixed else (None, None)
            if not fixed:
                tokens = key.split(".")
                if len(tokens) >= 2 and tokens[-1] in PARTS:
                    metric, part = tokens[-2:]
            if metric not in VALUE_METRICS | {"refund_ratio"} or part not in PARTS:
                continue
            entity = f["entity_id"]
            sku_coverage = p.get("component_coverage", {}).get(entity)
            if sku_coverage is None and fixed and entity in scope["skus"]:
                # Fixed v4 stores exact per-entity coverage, not a global substitute.
                sku_coverage = p.get("entity_coverage", {}).get(entity)
            cov = sku_coverage or coverage
            relevant = ("current", "comparison") if part == "delta" else (part,)
            usable = all(
                cov.get(k, {}).get("complete") is True and cov[k].get("expected_records", 0) > 0
                for k in relevant
            )
            expected_span = p.get("comparison_period") if part == "comparison" else p.get("period")
            if fixed and part == "comparison":
                expected_span = p.get("previous_period", {})
            span = (
                {"start": f.get("period_start"), "end": f.get("period_end")}
                if fixed
                else f.get("period", {})
            )
            usable = (
                usable
                and expected_span is not None
                and all(span.get(k) == expected_span.get(k) for k in ["start", "end"])
            )
            facts.append(
                {
                    "evidence_id": e["id"],
                    "metric_key": key,
                    "metric": metric,
                    "part": part,
                    "entity_id": entity,
                    "unit": f["unit"],
                    "value": f["value"],
                    "period": span,
                    "usable": usable,
                }
            )
    return facts


def _same(evidence, scope):
    return [
        e
        for e in evidence
        if sorted(e["payload"].get("scope", {}).get("skus", [])) == sorted(scope["skus"])
    ]


def _coverage(p):
    return (
        {
            "current": p.get("coverage", {}),
            "comparison": p.get("previous_period", {}).get("coverage", {}),
        }
        if p.get("review_profile") == "commerce_review"
        else p.get("coverage", {})
    )


def value_obligations(query, evidence, scope):
    c = request_contract(query)
    available = views(evidence, scope)
    identities = scope["skus"] if c["per_entity"] else [",".join(sorted(scope["skus"]))]
    result = []
    for identity in identities:
        for metric in c["value_metrics"]:
            for part in c["metric_periods"].get(metric, c["periods"]):
                unit = "count" if metric.endswith("units") else "CNY"
                selected = next(
                    (
                        f
                        for f in available
                        if f["entity_id"] == identity
                        and f["metric"] == metric
                        and f["part"] == part
                        and f["unit"] == unit
                        and f["usable"]
                    ),
                    None,
                )
                covs = []
                for e in evidence:
                    p = e["payload"]
                    if identity in p.get("component_coverage", {}):
                        covs.append(p["component_coverage"][identity])
                    elif identity in p.get("entity_coverage", {}):
                        covs.append(p["entity_coverage"][identity])
                    elif sorted(p.get("scope", {}).get("skus", [])) == (
                        [identity] if c["per_entity"] else sorted(scope["skus"])
                    ):
                        covs.append(_coverage(p))
                periods = ("current", "comparison") if part == "delta" else (part,)
                na = bool(
                    c["per_entity"]
                    and any(
                        cov.get(k, {}).get("expected_records") == 0 for cov in covs for k in periods
                    )
                )
                missing = any(
                    k in cov
                    and (
                        cov[k].get("complete") is not True or cov[k].get("expected_records", 0) == 0
                    )
                    for cov in covs
                    for k in periods
                )
                status = (
                    "acquired"
                    if selected
                    else "not_applicable"
                    if na
                    else "missing_source"
                    if missing
                    else "not_queried"
                )
                result.append(
                    {
                        "sku": identity,
                        "metric": metric,
                        "part": part,
                        "status": status,
                        "fact_key": selected["metric_key"] if selected else None,
                        "evidence_id": selected["evidence_id"] if selected else None,
                    }
                )
    return result


def amount_witness(evidence, scope):
    candidates = _same(evidence, scope)
    bindings = {
        (
            p.get("bundle_hash"),
            p.get("source_hashes", {}).get("sales", p.get("dataset_hash")),
            str(p.get("period")),
            str(p.get("comparison_period", p.get("previous_period", {})).get("start")),
            str(p.get("comparison_period", p.get("previous_period", {})).get("end")),
        )
        for e in candidates
        if (p := e["payload"]).get("fact_catalog")
        and p.get("tool") in {"compare_periods", "rank_contributors", "analyze_refunds", "sales"}
    }
    if len(bindings) != 1:
        return None
    available = views(candidates, scope)
    identity = ",".join(sorted(scope["skus"]))
    bound = []
    numbers = {}
    try:
        for metric in MONEY:
            for part in PARTS:
                f = next(
                    (
                        f
                        for f in available
                        if f["entity_id"] == identity
                        and f["metric"] == metric
                        and f["part"] == part
                        and f["unit"] == "CNY"
                        and f["usable"]
                    ),
                    None,
                )
                if f is None:
                    return None
                value = Decimal(f["value"]) * 100
                if not value.is_finite() or value != value.to_integral_value():
                    return None
                numbers[(metric, part)] = int(value)
                bound.append({k: f[k] for k in ["evidence_id", "metric_key"]})
        if any(
            numbers[("net_revenue", p)]
            != numbers[("gross_revenue", p)] - numbers[("refund_amount", p)]
            for p in PARTS
        ):
            return None
        if any(
            numbers[(m, "delta")] != numbers[(m, "current")] - numbers[(m, "comparison")]
            for m in MONEY
        ):
            return None
    except (InvalidOperation, ValueError, TypeError):
        return None
    return {
        "capability": "accounting_amount_identity",
        "scope": {"skus": sorted(scope["skus"])},
        "delta_cents": {m: numbers[(m, "delta")] for m in MONEY},
        "fact_refs": bound,
    }


def _record(code, message, eid, scope, period=None):
    return {
        "code": code,
        "message": message,
        "scope": {"skus": sorted(scope["skus"])},
        "period": period,
        "evidence_refs": [eid],
    }


def data_gaps(query, evidence, scope):
    c = request_contract(query)
    result = []
    sales_goals = set(c["goals"]) & {"net_revenue", "refunds", "products", "daily", "coverage"}
    if sales_goals or c["value_metrics"]:
        wanted = set(c["periods"]) - {"delta"}
        if "delta" in c["periods"]:
            wanted.update(["current", "comparison"])
        if c["boundary_intent"]:
            if "前期" in query or "比较期" in query or "两期" in query:
                wanted.add("comparison")
        for e in _same(evidence, scope):
            p = e["payload"]
            if p.get("tool") != "compare_periods" and p.get("review_profile") != "commerce_review":
                continue
            for part in sorted(wanted):
                cov = _coverage(p).get(part, {})
                if cov and (cov.get("complete") is not True or cov.get("expected_records") == 0):
                    code = (
                        "period_not_applicable"
                        if cov.get("expected_records") == 0
                        else "missing_sales_rows"
                    )
                    name = "本期" if part == "current" else "比较期"
                    result.append(
                        _record(
                            code,
                            f"{name}适用记录不足，不能据此作完整两期比较或归因",
                            e["id"],
                            scope,
                            part,
                        )
                    )
    if "refunds" in c["goals"] and not c["amount_identity_requested"]:
        for f in views(evidence, scope):
            if (
                f["metric"] == "paid_units"
                and f["part"] in {"current", "comparison"}
                and f["usable"]
                and Decimal(f["value"]) == 0
            ):
                result.append(
                    _record(
                        "zero_paid_denominator",
                        "支付分母为零，记账退款件数占比不可定义",
                        f["evidence_id"],
                        scope,
                        f["part"],
                    )
                )
    if "inventory" in c["goals"]:
        for e in _same(evidence, scope):
            if e["payload"].get("tool") != "inventory_risk":
                continue
            for item in e["payload"].get("items", []):
                if item.get("reason"):
                    messages = {
                        "missing_lead_days": "缺少配置提前期，不能作提前期阈值比较",
                        "missing_inventory": "缺少库存快照",
                        "stale_snapshot": "快照超过时点策略范围",
                        "future_snapshot": "快照晚于分析期末",
                        "zero_demand": "支付件数为零，覆盖天数代理不可计算",
                        "incomplete_sales_window": "销售窗口缺行或不适用",
                    }
                    result.append(
                        _record(
                            item["reason"],
                            f"{item['sku']}：{messages.get(item['reason'], '库存代理条件未齐备')}",
                            e["id"],
                            {"skus": [item["sku"]]},
                        )
                    )
    # Stable identities, no unbounded duplicate gaps from equivalent paths.
    seen = set()
    unique = []
    for item in result:
        identity = (item["code"], tuple(item["scope"]["skus"]), item["period"])
        if identity not in seen:
            unique.append(item)
            seen.add(identity)
    return unique


def requirements(query, evidence, scope, steps=()):
    c = request_contract(query)
    same = _same(evidence, scope)
    result = []

    def add(kind, status, tools=(), refs=()):
        result.append(
            {
                "requirement_id": kind,
                "status": status,
                "tools": list(tools),
                "evidence_refs": list(refs),
            }
        )

    values = value_obligations(query, evidence, scope)
    for v in values:
        add(
            f"{v['sku']}/{v['metric']}/{v['part']}",
            v["status"],
            ["rank_contributors"] if c["per_entity"] else ["compare_periods", "analyze_refunds"],
            [v["evidence_id"]] if v["evidence_id"] else [],
        )
    comparisons = [
        e
        for e in same
        if e["payload"].get("tool") == "compare_periods"
        and e["payload"].get("metric") == "net_revenue"
        or e["payload"].get("review_profile") == "commerce_review"
    ]
    sales_gap = any(
        g["code"] in {"missing_sales_rows", "period_not_applicable"}
        for g in data_gaps(query, evidence, scope)
    )
    if not c["pure_boundary"] and "net_revenue" in c["goals"] and not c["value_metrics"]:
        add(
            "net_scope",
            "acquired" if comparisons else "not_queried",
            ["compare_periods"],
            [e["id"] for e in comparisons],
        )
    if c["pure_boundary"] and set(c["goals"]) & {"coverage", "net_revenue"}:
        add(
            "source_coverage_explanation",
            "acquired"
            if comparisons and all(_coverage(e["payload"]) for e in comparisons)
            else "not_queried",
            ["compare_periods"],
            [e["id"] for e in comparisons],
        )
    for dim in c["contribution_dimensions"]:
        matches = []
        for e in same:
            p = e["payload"]
            if (
                p.get("tool") == "rank_contributors"
                and p.get("dimension") == dim
                and p.get("status") == "ok"
                and p.get("reconciliation", {}).get("balanced") is True
            ):
                matches.append(e["id"])
            if (
                p.get("review_profile") == "commerce_review"
                and p.get("previous_period", {}).get("comparable")
                and p.get("dimensions", {}).get("products" if dim == "sku" else "categories")
            ):
                matches.append(e["id"])
        add(
            "contribution_" + dim,
            "acquired" if matches else "missing_source" if sales_gap else "not_queried",
            ["rank_contributors"],
            matches,
        )
    if c["amount_identity_requested"]:
        witness = amount_witness(evidence, scope)
        add(
            "accounting_amount_identity",
            "acquired" if witness else "missing_source" if sales_gap else "not_queried",
            ["compare_periods", "analyze_refunds"],
            [x["evidence_id"] for x in witness["fact_refs"]] if witness else [],
        )
    refund_needed = (
        "refunds" in c["goals"]
        and not c["matrix_literal"]
        and not c["amount_identity_requested"]
        and not c["pure_boundary"]
    )
    if refund_needed:
        avail = views(evidence, scope)
        entity = ",".join(sorted(scope["skus"]))
        periods = [p for p in c["periods"] if p != "delta"] or ["current", "comparison"]
        needed = [
            (m, p) for m in ["paid_units", "refunded_units", "refund_amount"] for p in periods
        ]
        have = all(
            any(
                f["entity_id"] == entity and f["metric"] == m and f["part"] == p and f["usable"]
                for f in avail
            )
            for m, p in needed
        )
        ratios = all(
            any(
                f["entity_id"] == entity
                and f["part"] == p
                and f["usable"]
                and (
                    f["metric"] == "refund_ratio"
                    or f["metric"] == "paid_units"
                    and Decimal(f["value"]) == 0
                )
                for f in avail
            )
            for p in periods
        )
        add(
            "refund_accounting_ratio",
            "acquired" if have and ratios else "missing_source" if sales_gap else "not_queried",
            ["analyze_refunds"],
        )
    if "inventory" in c["goals"]:
        stock = [e for e in same if e["payload"].get("tool") == "inventory_risk"]
        items = [i for e in stock for i in e["payload"].get("items", [])]
        complete = {i["sku"] for i in items} == set(scope["skus"])
        usable = complete and all(
            i.get("snapshot_day")
            and i.get("sales_coverage", {}).get("complete")
            and i.get("coverage_days") is not None
            and (c["boundary_intent"] or i.get("below_lead_proxy") is not None)
            for i in items
        )
        add(
            "inventory_proxy",
            "acquired" if usable else "missing_source" if complete else "not_queried",
            ["inventory_risk"],
            [e["id"] for e in stock],
        )
    if "rules" in c["goals"] or "rule_applicability" in c["goals"]:
        quoted = [
            e["id"]
            for e in evidence
            if e["payload"].get("tool") == "search_rule_documents" and e["payload"].get("quotes")
        ]
        add(
            "literal_rule_quotes",
            "acquired" if quoted else "not_queried",
            ["search_rule_documents"],
            quoted,
        )
    if c["rule_interpretation_requested"]:
        interpreted = any(
            s["status"] == "SUCCEEDED" and s["proposal"].get("rule_interpretations") for s in steps
        )
        add(
            "requested_unverified_rule_interpretation", "acquired" if interpreted else "not_queried"
        )
    if "daily" in c["goals"]:
        covered = set()
        refs = []
        for e in evidence:
            p = e["payload"]
            if (
                p.get("tool") == "drilldown_series"
                and p.get("granularity") == "day"
                and p.get("series")
                and all(i["coverage"]["complete"] for i in p["series"])
            ):
                covered.update(p["scope"]["skus"])
                refs.append(e["id"])
        add(
            "daily_coverage",
            "acquired"
            if set(scope["skus"]) <= covered
            else "missing_source"
            if sales_gap
            else "not_queried",
            ["drilldown_series"],
            refs,
        )
    if "net_revenue" in c["goals"] and not (
        c["matrix_literal"]
        or c["boundary_intent"]
        or c["contribution_dimensions"]
        or c["amount_identity_requested"]
    ):
        from app.diagnosis.planner_v9 import dynamic_scope_covered

        done = dynamic_scope_covered(evidence, scope) or any(
            e["payload"].get("review_profile") == "commerce_review" for e in same
        )
        add(
            "requested_exploration",
            "acquired" if done else "missing_source" if sales_gap else "not_queried",
            ["rank_contributors", "analyze_refunds"],
        )
    for metric in c["unsupported_requests"]:
        add("unsupported_" + metric, "unsupported")
    return result


def missing_coverage(query, evidence, scope, steps=()):
    items = requirements(query, evidence, scope, steps)
    gaps = [g["message"] for g in data_gaps(query, evidence, scope)]
    for item in items:
        if item["status"] not in {"acquired", "not_applicable"}:
            gaps.append(
                "请求指标 "
                + item["requirement_id"][12:]
                + "：当前固定数据或只读工具不支持；保留其他可回答项"
                if item["status"] == "unsupported"
                else "必答项 " + item["requirement_id"] + " 尚未取得"
            )
    return list(dict.fromkeys(gaps))


def completion(query, evidence, scope, steps=(), *, report=None, stop_reason=None):
    c = request_contract(query)
    items = requirements(query, evidence, scope, steps)
    gaps = data_gaps(query, evidence, scope)
    displayed = None
    if report is not None:
        displayed = (
            {(f["evidence_id"], f["metric_key"]) for f in report["facts"]}
            if "facts" in report
            else {
                (m["evidence_id"], m["key"])
                for s in report.get("sections", [])
                for claim in s["claims"]
                for m in claim.get("metrics", [])
            }
        )
    omitted = []
    for v in value_obligations(query, evidence, scope):
        if (
            displayed is not None
            and v["status"] == "acquired"
            and (v["evidence_id"], v["fact_key"]) not in displayed
        ):
            omitted.append(f"{v['sku']}/{v['metric']}/{v['part']}")
    if displayed is not None:
        omitted.extend(
            f"{eid}/{key}"
            for eid, key in required_detail_refs(query, evidence, scope)
            if (eid, key) not in displayed
        )
    pending = [
        i["requirement_id"] for i in items if i["status"] not in {"acquired", "not_applicable"}
    ]
    # Data unavailability is not completed numeric work. Only an explicit boundary
    # question with no unqueried/omitted/computational requirements may complete.
    ready = not pending and not omitted
    failed = stop_reason in TERMINAL_FAILURES
    status = (
        "incomplete"
        if failed or not ready
        else "boundary_explained"
        if gaps and c["boundary_intent"]
        else "incomplete"
        if gaps
        else "fulfilled"
    )
    return {
        "status": status,
        "data_status": "partial"
        if gaps
        else "unverified"
        if any(i["status"] == "not_queried" for i in items)
        else "complete",
        "requirements": items,
        "data_gaps": gaps,
        "omitted_items": omitted,
        "remaining_work": list(dict.fromkeys(pending + omitted)),
    }


def required_detail_refs(query, evidence, scope):
    """Only requested inventory and explicit daily details; presentation cannot hide them."""
    c = request_contract(query)
    refs = []
    for e in evidence:
        p = e["payload"]
        if (
            "inventory" in c["goals"]
            and p.get("tool") == "inventory_risk"
            and set(p.get("scope", {}).get("skus", [])) == set(scope["skus"])
        ):
            refs.extend(
                (e["id"], key)
                for item in p.get("items", [])
                for key in item.get("fact_keys", {}).values()
                if key in p.get("fact_catalog", {})
            )
        if (
            "daily" in c["goals"]
            and p.get("tool") == "drilldown_series"
            and set(p.get("scope", {}).get("skus", [])) <= set(scope["skus"])
        ):
            metrics = set(c["value_metrics"]) or {"net_revenue"}
            refs.extend(
                (e["id"], key)
                for key in p.get("fact_catalog", {})
                if key.startswith("series.") and key.split(".")[-1] in metrics
            )
    return list(dict.fromkeys(refs))
