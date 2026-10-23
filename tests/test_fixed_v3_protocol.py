"""Both model-facing contexts retain v3 rules; malformed advice cannot escape."""

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr, SecretStr

from app.tasks.worker import Worker, checkpoint_store
from tests.fake_model import ScriptedCommerceModel
from tests.test_review_bound_inputs import review_task


class ActionModel(ScriptedCommerceModel):
    bad_action: bool = False
    _contexts: list = PrivateAttr(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        names = [t["function"]["name"] for t in kwargs.get("tools", [])]
        if "ReviewNarrativeV3" in names:
            data = json.loads(messages[-1].content)
            payload = data["facts"][0]["payload"]
            self._contexts.append(data)
            args = {
                "analysis": ["data_quality"],
                "recommendations": [
                    {
                        "action_id": "fill_pre_activation_zero"
                        if self.bad_action
                        else "verify_activation",
                        "precondition_id": "activation_boundary",
                        "scope_skus": payload["scope"]["skus"],
                    }
                ],
            }
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {"name": "ReviewNarrativeV3", "args": args, "id": "actions"}
                            ],
                            usage_metadata={
                                "input_tokens": 10,
                                "output_tokens": 5,
                                "total_tokens": 15,
                            },
                        )
                    )
                ]
            )
        if "SemanticReview" in names:
            self._contexts.append(json.loads(messages[-1].content.split("\n证据：\n")[0]))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.mark.parametrize("bad_action", [False, True])
async def test_fixed_v3_protocol_context_and_invalid_action_fallback(setup, bad_action):
    settings, repo, _, _ = setup
    settings.commerce_review_answer_version = 3
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-protocol-only")
    task, _, _ = review_task(setup)
    settings.commerce_review_answer_version = 1
    model = ActionModel(bad_action=bad_action)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert done["validation"]["passed"], done
    assert model._contexts[0]["facts"][0]["payload"]["direct_answers"]
    assert model._contexts[0]["facts"][0]["payload"]["allowed_actions"]
    assert "review-direct-answers" in str(model._contexts[1]["report"])
    assert len(model._contexts[1]["report"]["sections"]) == 5
    assert done["validation"]["draft_fallback_used"] is bad_action
    assert (
        "启用前不适用" in str(done["report"])
        if not bad_action
        else "缺失记录补为零" in str(done["report"])
    )
