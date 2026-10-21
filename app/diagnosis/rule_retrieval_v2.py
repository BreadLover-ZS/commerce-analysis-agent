"""Bounded lexical recall in an already-authorized manifest; no applicability claim."""

from app.diagnosis.extensions import search_rules

ALIASES = {
    "库存": {"覆盖", "补货", "提前期"},
    "inventory": {"库存", "覆盖", "补货"},
    "stock": {"库存", "覆盖", "补货"},
    "提前期": {"交期", "补货"},
    "退款": {"退货", "售后"},
}


def search_rules_v2(chunks, manifest_id, query, limit):
    terms = sorted(
        {term for word, values in ALIASES.items() if word in query.lower() for term in values}
    )
    result = search_rules(chunks, manifest_id, query + " " + " ".join(terms), limit)
    result.update(query=query, retrieval_method="manifest_lexical_v2", expanded_terms=terms)
    result["limitations"].append("同义词仅扩大固定清单内的候选召回，不证明条款相关性或适用性。")
    return result
