"""Bounded source selection. Full sources and hashes remain in the evidence ledger."""

import json
from collections import deque

from app.commerce.service import terms


def _window(text, query, length=1800):
    if len(text) <= length:
        return text, 0, len(text)
    words = set(terms(query))
    matches = [text.lower().find(word) for word in words if word in text.lower()]
    start = max(0, min(matches, default=0) - length // 3)
    start = min(start, len(text) - length)
    return text[start : start + length], start, start + length


def evidence_package(evidence, query="", max_sources=18, max_chars=28000):
    selected, excluded, truncations = [], [], []
    metrics = [e for e in evidence if e["kind"] == "metrics"]
    groups = {}
    query_terms = set(terms(query))
    text_sources = [e for e in evidence if e["kind"] != "metrics"]
    text_sources.sort(
        key=lambda e: (-len(query_terms.intersection(terms(e["title"] + e["excerpt"]))), e["id"])
    )
    for e in text_sources:
        groups.setdefault(e["kind"], deque()).append(e)
    ordered = list(metrics)
    while any(groups.values()):
        for group in groups.values():
            if group:
                ordered.append(group.popleft())
    by_document = {}
    for source in ordered:
        if len(selected) >= max_sources:
            excluded.append(source["id"])
            continue
        item = {
            key: source[key]
            for key in ("id", "kind", "title", "locator", "content_hash", "retrieved_at")
        }
        payload = source["payload"]
        if source["kind"] == "metrics":
            kept_items = payload.get("items", [])[:8]
            skus = {v["sku"] for v in kept_items}
            catalog = {
                k: v
                for k, v in payload["fact_catalog"].items()
                if not payload.get("items") or v["entity_id"] in skus
            }
            item["excerpt"] = "权威事实见payload.fact_catalog；完整计算结果保存在证据账本。"
            item["payload"] = {
                "sample": payload.get("sample"),
                "tool": payload["tool"],
                "metrics": {k: payload["metrics"][k] for k in catalog},
                "fact_catalog": catalog,
                "items": kept_items,
                "coverage": payload.get("coverage"),
                "period": payload["period"],
                "previous_period": payload.get("previous_period"),
                "limitations": payload.get("limitations", []),
                "definitions": payload.get("definitions", {}),
            }
            omitted = len(payload.get("items", [])) - len(kept_items)
            if omitted:
                truncations.append({"id": source["id"], "omitted_inventory_items": omitted})
        else:
            document = source["locator"].split("/chars=")[0].split("/lines=")[0]
            if source["kind"] == "document" and by_document.get(document, 0) >= 2:
                excluded.append(source["id"])
                continue
            excerpt, start, end = _window(source["excerpt"], query)
            item["excerpt"] = excerpt
            item["payload"] = {
                "sample": payload.get("sample", False),
                "support_scope": payload.get("support_scope", "引用窗口，不是完整文档"),
                "excerpt_start": start,
                "excerpt_end": end,
                "excerpt_truncated": len(excerpt) < len(source["excerpt"]),
            }
            if len(excerpt) < len(source["excerpt"]):
                truncations.append(
                    {
                        "id": source["id"],
                        "start": start,
                        "end": end,
                        "full_characters": len(source["excerpt"]),
                    }
                )
            by_document[document] = by_document.get(document, 0) + 1
        encoded = json.dumps([*selected, item], ensure_ascii=False)
        if len(encoded) > max_chars:
            excluded.append(source["id"])
            continue
        selected.append(item)
    manifest = {
        "selected_ids": [e["id"] for e in selected],
        "excluded_ids": excluded,
        "truncations": truncations,
        "characters": len(json.dumps(selected, ensure_ascii=False)),
        "max_characters": max_chars,
        "max_sources": max_sources,
        "policy": "metrics-first/source-round-robin/query-window",
    }
    return {"sources": selected, "manifest": manifest}
