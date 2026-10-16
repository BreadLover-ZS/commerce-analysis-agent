"""End-to-end Docker smoke test using ONLY the configured local demo account."""

import argparse
import time
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--profile", choices=["research", "commerce_review"], default="research")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=10) as client:
        config = client.get("/api/config").json()
        if config.get("mode") != "demo" or not config.get("demo_accounts"):
            raise SystemExit("Smoke test requires the isolated demo deployment")
        login = client.post(
            "/api/auth/login", json={"email": "demo@example.com", "password": "demo-commerce-2026"}
        )
        login.raise_for_status()
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        conversation = client.post("/api/conversations")
        conversation.raise_for_status()
        dataset = None
        if args.profile == "commerce_review":
            staged = client.post("/api/commerce/scenarios", json={"scenario": "normal"})
            staged.raise_for_status()
            confirmed = client.post(f"/api/commerce/datasets/{staged.json()['id']}/commit")
            confirmed.raise_for_status()
            dataset = confirmed.json()
        payload = {
            "conversation_id": conversation.json()["id"],
            "query": "分析九月经营与库存",
            "sources": ["sales", "inventory"],
            "public_queries": [],
        }
        if dataset:
            payload.update(
                profile="commerce_review",
                dataset_id=dataset["id"],
                sources=["sales"],
                strategy="workflow",
            )
        response = client.post(
            "/api/tasks", headers={"Idempotency-Key": str(uuid4())}, json=payload
        )
        response.raise_for_status()
        task_id = response.json()["id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = client.get("/api/tasks/" + task_id)
            response.raise_for_status()
            task = response.json()
            if task["status"] in {"succeeded", "degraded"}:
                assert task["validation"]["passed"]
                if dataset:
                    evidence = client.get(f"/api/tasks/{task_id}/evidence")
                    evidence.raise_for_status()
                    metrics = evidence.json()["evidence"][0]["payload"]
                    assert metrics["dataset_hash"] == dataset["content_hash"]
                    assert metrics["metrics"]["net_revenue"] == "52800.00"
                    assert task["parameters"]["profile"] == "commerce_review"
                else:
                    assert (
                        task["report"]["sections"][0]["claims"][0]["metrics"][0]["value"]
                        == "83766.00"
                    )
                artifact = next(a for a in task["artifacts"] if a["name"] == "report.pdf")
                pdf = client.get("/api/artifacts/" + artifact["id"])
                pdf.raise_for_status()
                assert pdf.content.startswith(b"%PDF")
                print(
                    "PASS: authenticated task, PostgreSQL data, worker, evidence and PDF download"
                )
                return
            if task["status"] == "failed":
                raise SystemExit("Worker failed: " + str(task["error_code"]))
            time.sleep(0.5)
        raise SystemExit("Worker did not complete within 30 seconds")


if __name__ == "__main__":
    main()
