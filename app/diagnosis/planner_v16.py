"""v16 uses the frozen v15 obligations and explicitly identifies the new protocol."""

import json

from app.diagnosis.planner_v15 import prompt as v15_prompt


def prompt(task, bundle, scope, steps, evidence, catalog, *, base_prompt=None):
    messages = (base_prompt or v15_prompt)(task, bundle, scope, steps, evidence, catalog)
    context = json.loads(messages[1][1])
    context["prompt_version"] = "diagnosis-v16"
    budget = task["parameters"]["budget"]
    if "decision_call_reserve" in budget:
        from app.diagnosis.planner_budget import decision_limit

        limit = min(12, budget["max_model_calls"])
        used = task["metrics"].get("model_calls", 0)
        context["remaining_budget"].update(
            model_calls=max(0, limit - used),
            decision_calls=max(0, decision_limit(limit, budget) - used),
            decision_call_reserve=budget["decision_call_reserve"],
            current_request_included=True,
        )
        # DiagnosticMetrics.refunds() calls compare() for BOTH gross and refund
        # amounts. Advertise the actual output, not merely the tool's main use.
        frontier = context["capability_frontier"].get("analyze_refunds")
        if frontier is not None:
            frontier["outputs"] = [
                "scope.gross_revenue.current/comparison/delta",
                "scope.refund_amount.current/comparison/delta",
                "scope.paid_units.current/comparison/delta",
                "scope.refunded_units.current/comparison/delta",
                "scope.refund_ratio.current/comparison",
                "scope.net_change_identity",
            ]
            pending = {
                r["requirement_id"]
                for r in context["request_requirements"]
                if r["status"] == "not_queried"
            }
            if "accounting_amount_identity" in pending:
                frontier.update(
                    priority="cover_remaining_scope_amount_identity",
                    recommended_arguments={"filters": {"skus": list(scope["skus"])}},
                    scope_amount_identity_hint=(
                        "已有同范围完整净额基线时，本工具一次补齐支付与退款金额两期及差额，"
                        "供程序验证九项金额恒等式；不能替代逐SKU分量、品类或逐日要求。"
                    ),
                )
        messages[0] = (
            messages[0][0],
            messages[0][1]
            + "\n本次请求也计入remaining_budget。按冻结额度规划，优先选能覆盖多个未完成义务的工具；"
            "不重复已取得事实。analyze_refunds实际同时返回同范围支付与退款金额两期及差额。",
        )
    if task["parameters"].get("answer_boundary_version") == 2 and budget.get(
        "stop_after_model_failure"
    ):
        messages[0] = (
            messages[0][0],
            messages[0][1]
            + "\n本Task启用首错即停：任何错误提议或无信息步骤立即结束，不会付费纠错；请一次提交完整合法参数。",
        )
    return [("system", messages[0][1]), ("human", json.dumps(context, ensure_ascii=False))]
