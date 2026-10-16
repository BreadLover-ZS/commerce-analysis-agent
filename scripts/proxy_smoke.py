"""Isolated Compose login probes; never submit model tasks or target public services."""

import argparse
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


def verify_proxy(client, trusted):
    config = client.get("/api/config")
    config.raise_for_status()
    if config.json().get("mode") != "demo" or not config.json().get("demo_accounts"):
        raise ValueError("代理演练仅允许隔离demo；禁止对现有用户服务施加登录限制")
    prefix = uuid4().hex

    def attempt(address, email, password):
        return client.post(
            "/api/auth/login",
            headers={"X-Forwarded-For": address, "X-Forwarded-Proto": "https"},
            json={"email": email, "password": password},
        ).status_code

    for index in range(10):
        address = "203.0.113.10" if trusted else f"203.0.113.{index + 1}"
        if attempt(address, f"{prefix}-{index}@example.com", "invalid") != 401:
            raise ValueError("代理演练未到达预期的独立错误登录窗口")
    expected = 200 if trusted else 429
    if attempt("203.0.113.20", "demo@example.com", "demo-commerce-2026") != expected:
        raise ValueError("可信客户端隔离或未受信转发头拒绝不符合预期")
    if trusted:
        if attempt("203.0.113.10", f"{prefix}-blocked@example.com", "invalid") != 429:
            raise ValueError("原客户端限流被绕过")
        for index in range(10):
            if (
                attempt(f"198.51.100.{index + 1}", f"{prefix}-account@example.com", "invalid")
                != 401
            ):
                raise ValueError("账号限流演练未到达预期窗口")
        if attempt("198.51.100.20", f"{prefix}-account@example.com", "invalid") != 429:
            raise ValueError("跨IP账号限流失效")


def main():
    parser = argparse.ArgumentParser(description="仅在可丢弃Compose测试库中验证代理限流")
    parser.add_argument("--confirm-isolated-compose", action="store_true", required=True)
    parser.add_argument("--mode", choices=["trusted", "untrusted"], required=True)
    parser.add_argument("--url")
    args = parser.parse_args()
    url = args.url or (
        "http://frontend:8080" if args.mode == "trusted" else "http://127.0.0.1:8080"
    )
    target = urlsplit(url)
    if (
        target.scheme != "http"
        or target.hostname not in {"frontend", "127.0.0.1", "localhost"}
        or target.username
        or target.password
        or target.path not in {"", "/"}
        or target.query
        or target.fragment
    ):
        raise SystemExit("代理演练拒绝外部或带额外路径的目标")
    with httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
        verify_proxy(client, args.mode == "trusted")
    print("PASS: " + args.mode + " proxy headers and login isolation (demo only)")


if __name__ == "__main__":
    main()
