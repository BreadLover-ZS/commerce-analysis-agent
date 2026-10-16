"""Stable source identities and authoritative metric views over an append-only ledger."""

from urllib.parse import urlsplit


def metric_scope(payload):
    period = payload.get("period") or {}
    if not isinstance(period, dict):
        return None
    values = (payload.get("tool"), period.get("start"), period.get("end"))
    if values[0] in {"sales", "inventory"} and all(isinstance(v, str) and v for v in values):
        return values
    return None


def authoritative_evidence(evidence):
    """Ledger order is retrieval order; the first usable result owns each metric scope."""
    selected, superseded, seen = [], [], set()
    for source in evidence:
        scope = metric_scope(source.get("payload", {})) if source["kind"] == "metrics" else None
        if scope is not None and scope in seen:
            superseded.append(source["id"])
            continue
        if scope is not None:
            seen.add(scope)
        selected.append(source)
    return selected, superseded


def document_identity(source):
    payload = source.get("payload", {})
    provider, document = payload.get("provider"), payload.get("document_id")
    if isinstance(document, str) and document:
        if provider == "local":
            return ("local", document), False
        dataset = payload.get("dataset_id")
        if provider == "ragflow" and isinstance(dataset, str) and dataset:
            return ("ragflow", dataset, document), False
    # Historical evidence remains readable. Infer only known URI formats and disclose it.
    try:
        location = urlsplit(source.get("locator", ""))
    except ValueError:
        return None, False
    if location.scheme == "document" and location.netloc:
        return ("local", location.netloc), True
    parts = location.path.strip("/").split("/")
    if location.scheme == "ragflow" and location.netloc and parts[0]:
        return ("ragflow", location.netloc, parts[0]), True
    return None, False
