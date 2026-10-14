import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

from app.research.evidence import deterministic_report
from app.research.synthesis import excerpt_synthesis


class ScriptedCommerceModel(BaseChatModel):
    """Offline protocol double, not an LLM quality benchmark."""

    model_name: str = "fake-commerce"
    bad_report: bool = False
    _tool_sets: list[list[str]] = PrivateAttr(default_factory=list)
    _skill_loaded: bool = PrivateAttr(default=False)
    _research_forwarded: bool = PrivateAttr(default=False)

    @property
    def _llm_type(self):
        return "fake-commerce"

    def _get_ls_params(self, **kwargs):
        return {"ls_provider": "openai", "ls_model_name": self.model_name, "ls_model_type": "chat"}

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any):
        names = [t["function"]["name"] for t in kwargs.get("tools", [])]
        self._tool_sets.append(names)
        if any(
            isinstance(m, ToolMessage)
            and m.name == "read_file"
            and "用户单独提供的公开搜索关键词" in str(m.content)
            for m in messages
        ):
            self._skill_loaded = True
        content = messages[-1].content
        calls = []
        if "TaskPlan" in names:
            params = json.loads(content)
            args = {
                "objective": params["task"],
                "sources": params["allowed_sources"],
                "web_queries": params["allowed_public_queries"],
                "document_query": "经营分析 库存 退款",
            }
            calls = [{"name": "TaskPlan", "args": args, "id": "plan"}]
        elif "ResearchReport" in names:
            evidence = json.loads(content.split("证据：\n", 1)[1])
            metadata = json.loads(content.split("\n证据：\n", 1)[0])
            self._research_forwarded = bool(metadata.get("research_findings", {}).get("findings"))
            report = deterministic_report(evidence, [])
            if self.bad_report:
                report.sections[0].claims[0].evidence_ids = ["E-invented"]
            calls = [{"name": "ResearchReport", "args": report.model_dump(), "id": "draft"}]
        elif "ResearchSynthesis" in names:
            evidence = json.loads(content.split("证据：\n", 1)[1])
            synthesis = excerpt_synthesis(evidence)
            synthesis.gaps = []
            calls = [
                {"name": "ResearchSynthesis", "args": synthesis.model_dump(), "id": "synthesis"}
            ]
        elif "SemanticReview" in names:
            calls = [
                {
                    "name": "SemanticReview",
                    "args": {
                        "unsupported_claim_ids": [],
                        "explanation": "脚本化协议测试，不代表语义判断能力",
                    },
                    "id": "verify",
                }
            ]
        else:
            used = {m.name for m in messages if isinstance(m, ToolMessage)}
            if "read_file" in names and "read_file" not in used:
                calls = [
                    {
                        "name": "read_file",
                        "args": {"file_path": "/skills/competitor-research/SKILL.md"},
                        "id": "skill",
                    }
                ]
            elif "task" in names and "task" not in used:
                calls = [
                    {
                        "name": "task",
                        "args": {
                            "subagent_type": "market_researcher",
                            "description": "研究公开竞品；仅使用保温杯 品牌 官网作为公开关键词。",
                        },
                        "id": "delegate",
                    }
                ]
            elif (
                "task" not in names
                and "search_public_sources" in names
                and "search_public_sources" not in used
            ):
                calls = [
                    {
                        "name": "search_public_sources",
                        "args": {"query": "保温杯 品牌 官网"},
                        "id": "search",
                    }
                ]
        message = AIMessage(
            content="" if calls else "已完成允许范围内的证据收集。",
            tool_calls=calls,
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])
