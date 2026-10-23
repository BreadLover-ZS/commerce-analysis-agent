"""Deterministic authoring only: raw fixtures and independent expected arithmetic.

No Worker, graph or model execution. Refuse to overwrite a prepared packet.
Run from the repository: .venv/bin/python docs/acceptance/diagnosis-v11-h01-h20/prepare.py
"""

import copy
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from evaluations.diagnosis_cases import Case, load_cases, reference_answer  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    directory = Path(__file__).resolve().parent
    if (directory / "cases.json").exists():
        raise FileExistsError("Prepared cases are immutable; use a new packet directory")
    source = ROOT / "evaluations/diagnosis_cases.json"
    blueprint = directory.parent / "diagnosis-v10-h01-h20/cases.json"
    provenance = directory.parent / "diagnosis-v10-h01-h20/provenance.json"
    originals = {c["case_id"]: c for c in json.loads(source.read_bytes())["cases"]}
    previous = json.loads(blueprint.read_bytes())["cases"]
    old_origins = json.loads(provenance.read_bytes())["origins"]
    cases, origins = [], []
    for number, (old, origin) in enumerate(zip(previous, old_origins), 1):
        c = copy.deepcopy(originals[origin["development_source"]])
        factor = 80 + number
        mapping = {p["sku"]: f"V{number:02}{i}" for i, p in enumerate(c["sales"]["products"], 1)}

        def shift(value):
            return str(date.fromisoformat(value) + timedelta(days=31))

        for p in c["sales"]["products"]:
            p["sku"], p["active_from"] = mapping[p["sku"]], shift(p["active_from"])
        for row in c["sales"]["daily_sales"]:
            row["sku"], row["day"] = mapping[row["sku"]], shift(row["day"])
            for field in ("paid_units", "refunded_units", "revenue_cents", "refund_cents"):
                row[field] *= factor
        for part in ("period", "comparison_period"):
            c[part] = {k: shift(v) for k, v in c[part].items()}
        c["scope_skus"] = [mapping[k] for k in c["scope_skus"]]
        if c["inventory"]:
            c["inventory"]["snapshot_day"] = shift(c["inventory"]["snapshot_day"])
            for row in c["inventory"]["items"]:
                row["sku"], row["available_units"] = (
                    mapping[row["sku"]],
                    row["available_units"] * factor,
                )
                if number == 18:
                    row["lead_days"] = 6
        c.update(
            case_id=old["case_id"],
            split="holdout",
            execution="diagnostic",
            query=old["query"],
            expected=copy.deepcopy(old["expected"]),
            harness=copy.deepcopy(old["harness"]),
            documents=copy.deepcopy(old["documents"]),
        )
        for original, prior_sku in origin["sku_mapping"].items():
            c["query"] = c["query"].replace(prior_sku, mapping[original])
            if c["harness"]["clarification_choice"] == prior_sku:
                c["harness"]["clarification_choice"] = mapping[original]
        if number == 1:
            c["query"] = (
                "请对每个授权商品分别列出支付金额、退款金额和净销售额的本期值、比较期值及差额；逐商品核对净销售额变化贡献并对账。"
            )
            c["expected"]["required_assertions"].append("per_sku_components_displayed")
        if number == 5:
            c["query"] = (
                "请直接列出本期与比较期的净销售额及差额，并说明显式零记录与缺失记录的区别。"
            )
            c["expected"]["required_assertions"].append("comparison_net_displayed")
        if number == 8:
            c["expected"]["required_assertions"].append("activation_actions_bound")
        if number == 15:
            c["query"] = (
                f"仅核对商品 {c['scope_skus'][0]} 两期退款金额与记账期退款件数占比；不用计算利润，不扩大商品范围。"
            )
            c["expected"]["required_tools"] = ["analyze_refunds"]
            c["expected"]["required_assertions"].append("refund_answer_displayed")
        if number in (19, 20):
            for doc in c["documents"]:
                doc["text"] = "本轮业务核对修订戊。" + doc["text"]
        c["expected"].update(reference_answer(Case.model_validate(c)))
        cases.append(c)
        origins.append(
            {
                "case_id": c["case_id"],
                "development_source": origin["development_source"],
                "scale_factor": factor,
                "date_shift_days": 31,
                "sku_mapping": mapping,
            }
        )
    output = {
        "schema_version": 1,
        "oracle": "independent_raw_rows_v1",
        "splits": {"development": 0, "regression": 0, "holdout": 20},
        "cases": cases,
    }
    (directory / "cases.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    load_cases(directory / "cases.json")
    record = {
        "source": str(source.relative_to(ROOT)),
        "source_sha256": sha(source),
        "semantic_blueprint": str(blueprint.relative_to(ROOT)),
        "blueprint_sha256": sha(blueprint),
        "author": "AI code author; not external business blind assessment",
        "scope": "v11 corpus-local H01-H20; independent expected row arithmetic, no inherited scores",
        "model_or_graph_executions_during_authoring": 0,
        "limitations": "Synthetic development/regression-family variants; different hashes do not prove semantic independence.",
        "origins": origins,
    }
    (directory / "provenance.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    )
    print("Prepared 20 cases; raw-row arithmetic validated; graph/model executions: 0")


if __name__ == "__main__":
    main()
