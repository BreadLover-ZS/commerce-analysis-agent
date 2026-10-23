"""Explicit runtime versions. Never reinterpret a saved v1 checkpoint as v2."""

from app.diagnosis.schemas import GRAPH_VERSION, PROMPT_VERSION, TOOL_VERSION
from app.diagnosis.source_schemas import InventoryArgs, RuleSearchArgs

V1 = {
    "graph_version": GRAPH_VERSION,
    "schema_version": "1",
    "prompt_version": PROMPT_VERSION,
    "tool_registry_version": TOOL_VERSION,
}
V2 = {
    "graph_version": "diagnosis-v2",
    "schema_version": "2",
    "prompt_version": "diagnosis-v2",
    "tool_registry_version": "2",
}
V3 = {**V2, "graph_version": "diagnosis-v3", "prompt_version": "diagnosis-v3"}
V4 = {**V2, "graph_version": "diagnosis-v4", "prompt_version": "diagnosis-v4"}
V5 = {**V2, "graph_version": "diagnosis-v5", "prompt_version": "diagnosis-v5"}
V6 = {
    **V5,
    "graph_version": "diagnosis-v6",
    "prompt_version": "diagnosis-v6",
    "tool_registry_version": "3",
}
V7 = {**V6, "graph_version": "diagnosis-v7", "prompt_version": "diagnosis-v7"}
V8 = {**V7, "graph_version": "diagnosis-v8", "prompt_version": "diagnosis-v8"}
V9 = {**V8, "graph_version": "diagnosis-v9", "prompt_version": "diagnosis-v9"}
V10 = {
    **V9,
    "graph_version": "diagnosis-v10",
    "prompt_version": "diagnosis-v10",
    "tool_registry_version": "4",
}
V11 = {
    **V10,
    "graph_version": "diagnosis-v11",
    "prompt_version": "diagnosis-v11",
    "tool_registry_version": "5",
}
V12 = {**V11, "graph_version": "diagnosis-v12", "prompt_version": "diagnosis-v12"}
V13 = {
    **V12,
    "graph_version": "diagnosis-v13",
    "prompt_version": "diagnosis-v13",
    "schema_version": "3",
    "tool_registry_version": "6",
}
RUNTIME_VERSIONS = {
    v["graph_version"]: v for v in (V1, V2, V3, V4, V5, V6, V7, V8, V9, V10, V11, V12, V13)
}
EXTENSION_TOOLS = {"inventory_risk": InventoryArgs, "search_rule_documents": RuleSearchArgs}


def extended_objectives(query):
    return [
        name
        for name, terms in {
            "inventory": ("库存", "补货", "缺货", "stock", "inventory"),
            "rules": ("规则", "规定", "政策", "条款", "文档", "rule", "policy"),
        }.items()
        if any(term in query.lower() for term in terms)
    ]


def accepted_versions(query, sources, runtime="legacy"):
    if runtime == "v13":
        return dict(V13)
    if runtime == "v12":
        return dict(V12)
    if runtime == "v11":
        return dict(V11)
    if runtime == "v10":
        return dict(V10)
    if runtime == "v9":
        return dict(V9)
    if runtime == "v8":
        return dict(V8)
    if runtime == "v7":
        return dict(V7)
    if runtime == "v6":
        return dict(V6)
    if runtime == "v5":
        return dict(V5)
    if runtime == "v4":
        return dict(V4)
    if runtime == "v3":
        return dict(V3)
    if runtime != "legacy":
        raise ValueError("不支持的诊断运行契约")
    return dict(V2 if set(sources) - {"sales"} or extended_objectives(query) else V1)


def is_v2(task):
    return task["graph_version"] == V2["graph_version"]


def is_extended(task):
    return task["graph_version"] in {
        v["graph_version"] for v in (V2, V3, V4, V5, V6, V7, V8, V9, V10, V11, V12, V13)
    }


def tool_schemas(task):
    from app.diagnosis.schemas import SALES_TOOLS

    tools = dict(SALES_TOOLS)
    if is_extended(task):
        sources = task["parameters"]["sources"]
        if "inventory" in sources:
            tools["inventory_risk"] = InventoryArgs
        if "documents" in sources:
            tools["search_rule_documents"] = RuleSearchArgs
    return tools
