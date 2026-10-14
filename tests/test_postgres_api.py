import os

import pytest
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import authenticate
from tests.test_postgres import postgres_setup as postgres_setup

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="未提供隔离 PostgreSQL 测试服务"
)


@pytest.mark.usefixtures("postgres_setup")
async def test_authenticated_api_and_worker_with_non_superuser_postgres(postgres_setup):
    from uuid import uuid4

    settings, repo, _ = postgres_setup
    with TestClient(create_app(settings, repo)) as client:
        authenticate(client)
        conversation = client.post("/api/conversations").json()["id"]
        response = client.post(
            "/api/tasks",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "conversation_id": conversation,
                "query": "分析经营表现与库存",
                "sources": ["sales", "inventory"],
            },
        )
        assert response.status_code == 202
        async with checkpoint_store(settings) as saver:
            await Worker(repo, settings, saver).run_once()
        done = client.get("/api/tasks/" + response.json()["id"]).json()
        assert done["validation"]["passed"]
        pdf = next(a for a in done["artifacts"] if a["name"] == "report.pdf")
        assert client.get("/api/artifacts/" + pdf["id"]).content.startswith(b"%PDF")
