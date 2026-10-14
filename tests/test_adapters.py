import pytest
from pydantic import SecretStr

from app.research.tools import ToolGateway
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import new_task
from tests.fake_model import ScriptedCommerceModel


async def test_ragflow_raw_chunks_and_dataset_boundary(setup, monkeypatch):
    settings, repo, _, _ = setup
    settings.knowledge_provider = "ragflow"
    settings.ragflow_api_url = "https://configured-service.example"
    settings.ragflow_api_key = SecretStr("offline-adapter-test")
    settings.ragflow_datasets = {"demo": ["allowed"]}
    task = new_task(setup, sources=["documents"])
    ctx = repo.claim()
    gateway = ToolGateway(repo, settings, ctx, task["parameters"])

    async def response(url, body, headers):
        assert url.endswith("/api/v1/retrieval") and body["dataset_ids"] == ["allowed"]
        assert headers["Authorization"] == "Bearer offline-adapter-test"
        return {
            "code": 0,
            "data": {
                "chunks": [
                    {
                        "dataset_id": "allowed",
                        "document_id": "doc",
                        "id": "chunk",
                        "document_keyword": "制度",
                        "content": "库存需要核对供应商交期。",
                        "positions": [],
                    }
                ]
            },
        }

    monkeypatch.setattr(gateway, "_post", response)
    result = await gateway.documents("库存规则")
    assert result["evidence"][0]["locator"] == "ragflow://allowed/doc/chunk"
    assert result["evidence"][0]["payload"]["positions"] == []

    async def forbidden(*_):
        return {"code": 0, "data": {"chunks": [{"dataset_id": "other", "content": "private"}]}}

    monkeypatch.setattr(gateway, "_post", forbidden)
    with pytest.raises(ValueError):
        await gateway.documents("其他数据")
    assert len(repo.evidence(task["id"])) == 1
    repo.finish(ctx, "cancelled", 0)


async def test_demo_task_stays_offline_after_mode_change(setup):
    settings, repo, _, _ = setup
    task = new_task(setup)
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-test")
    model = ScriptedCommerceModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert done["mode"] == "demo" and done["metrics"]["model_calls"] == 0
    assert model._tool_sets == []
    assert any(e["kind"] == "fixture" for e in repo.evidence(task["id"]))
