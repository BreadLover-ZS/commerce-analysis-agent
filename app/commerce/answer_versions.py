"""Select semantics from a frozen server field; absent fields mean legacy v1."""


def boundary_version(parameters):
    value = parameters.get("answer_boundary_version", 1)
    if type(value) is not int or value not in {1, 2}:
        raise ValueError("invalid_frozen_answer_boundary_version")
    return value


def contract_module(parameters):
    if boundary_version(parameters) == 2:
        from app.commerce import answer_contract_v5

        return answer_contract_v5
    from app.commerce import answer_contract_v4

    return answer_contract_v4


def completion_module(parameters):
    if boundary_version(parameters) == 2:
        from app.diagnosis import completion_boundary_v2

        return completion_boundary_v2
    from app.diagnosis import completion_v15

    return completion_v15
