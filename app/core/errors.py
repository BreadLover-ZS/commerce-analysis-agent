"""Fixed provider diagnostics; never retain message text, headers or request bodies."""


def provider_error_data(error):
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    name = type(error).__name__
    if status in {401, 403}:
        category = "authentication"
    elif status == 429:
        category = "rate_limit"
    elif status is not None and status >= 500:
        category = "service_unavailable"
    elif status is not None and status >= 400:
        category = "request_rejected"
    elif isinstance(error, TimeoutError) or name in {
        "APITimeoutError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
    }:
        category = "timeout"
    elif name in {
        "APIConnectionError",
        "ConnectError",
        "ReadError",
        "WriteError",
        "RemoteProtocolError",
    }:
        category = "connection"
    else:
        category = "unknown"
    return {"error_type": name, "http_status": status, "category": category}
