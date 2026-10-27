"""One calculation for frozen decision budgets; absent policy preserves legacy behavior."""


def decision_limit(limit, budget):
    reserve = budget.get("decision_call_reserve", 3)
    if type(reserve) is not int or reserve < 0 or reserve > 11:
        raise ValueError("invalid_frozen_decision_call_reserve")
    if "decision_call_reserve" in budget and reserve >= limit:
        raise ValueError("decision_reserve_must_be_below_call_limit")
    return limit - reserve if limit > reserve else limit
