from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.server import create_app
from app.commerce.service import seed_commerce
from app.core.auth import bootstrap_user
from app.core.config import Settings
from app.research.schemas import TaskRequest
from app.storage.models import Base, Task, User, utcnow
from app.storage.repository import Repository


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        _env_file=None,
        app_env="test",
        diagnosis_runtime="legacy",
        commerce_review_answer_version=1,
        data_dir=tmp_path,
        database_url="sqlite:///" + str(tmp_path / "app.sqlite"),
        lease_seconds=5,
    )
    repo = Repository(settings)
    Base.metadata.create_all(repo.engine)
    bootstrap_user(repo, settings)
    seed_commerce(repo)
    with repo.session() as db:
        user = db.scalar(select(User).where(User.email == "demo@example.com"))
        other = db.scalar(select(User).where(User.email == "other@example.com"))
    yield settings, repo, user, other
    repo.engine.dispose()


def new_task(setup, **options):
    settings, repo, user, _ = setup
    request = TaskRequest(
        conversation_id=repo.create_conversation(user.id),
        query=options.pop("query", "分析经营表现、库存与竞品，注明依据"),
        public_queries=["保温杯 品牌 官网"],
        **options,
    )
    return repo.create_task(user, request, str(uuid4()))


def expire_lease(repo, task_id):
    with repo.session() as db:
        db.get(Task, task_id).lease_until = utcnow() - timedelta(seconds=1)
        db.commit()


@pytest.fixture
def client(setup):
    settings, repo, *_ = setup
    with TestClient(create_app(settings, repo)) as client:
        yield client


def authenticate(client, email="demo@example.com", password="demo-commerce-2026"):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response
