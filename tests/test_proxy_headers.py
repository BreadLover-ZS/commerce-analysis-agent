import re
from pathlib import Path

import httpx
import pytest
from uvicorn.config import Config
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.server import create_app
from scripts.proxy_smoke import verify_proxy


@pytest.mark.parametrize("trusted", [True, False])
async def test_compose_proxy_configuration_preserves_client_and_account_limits(
    setup, monkeypatch, trusted
):
    settings, repo, *_ = setup
    # Read the actual Uvicorn environment declared in Compose, not a wildcard test override.
    compose = Path("docker/docker-compose.yaml").read_text()
    match = re.search(r"FORWARDED_ALLOW_IPS: \$\{COMMERCE_FRONTEND_IP:-([^}]+)\}", compose)
    assert match, "Compose must declare an explicit trusted frontend address"
    frontend = match[1]
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", frontend)
    config = Config("app.api.server:app")
    assert config.forwarded_allow_ips == frontend
    app = ProxyHeadersMiddleware(
        create_app(settings, repo), trusted_hosts=config.forwarded_allow_ips
    )
    peer = frontend if trusted else "192.0.2.10"
    transport = httpx.ASGITransport(app=app, client=(peer, 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:

        async def attempt(ip, email, password="invalid"):
            return await client.post(
                "/api/auth/login",
                headers={"X-Forwarded-For": ip},
                json={"email": email, "password": password},
            )

        for index in range(10):
            address = "203.0.113.10" if trusted else f"203.0.113.{index + 1}"
            assert (await attempt(address, f"absent-{index}@example.com")).status_code == 401
        result = await attempt("203.0.113.20", "demo@example.com", "demo-commerce-2026")
        assert result.status_code == (200 if trusted else 429)
        if trusted:
            for index in range(10):
                assert (
                    await attempt(f"198.51.100.{index + 1}", "same-absent@example.com")
                ).status_code == 401
            assert (await attempt("198.51.100.20", "same-absent@example.com")).status_code == 429


def test_proxy_smoke_refuses_non_demo_before_login():
    def response(request):
        assert request.url.path == "/api/config"
        return httpx.Response(200, json={"mode": "live", "demo_accounts": False})

    with httpx.Client(
        base_url="http://frontend:8080", transport=httpx.MockTransport(response)
    ) as client:
        with pytest.raises(ValueError, match="仅允许隔离demo"):
            verify_proxy(client, trusted=True)
