import json
import time
from typing import Any

import anthropic

from .. import costs
from ..config import get_settings
from ..logging_setup import log
from ..retry import PermanentError
from .base import ResearchResult

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_PAUSE_CONTINUATIONS = 6

# 研究與腳本（含其審查、修改、事實整理）用主力模型；其餘工作用快速模型以節省成本
MAIN_MODEL_TASKS = {"research", "research_extract", "script", "script_review", "script_revise"}


class ClaudeProvider:
    """LLMProvider + ResearchProvider（web_search 由 Anthropic 伺服器端執行）。"""

    def __init__(self):
        s = get_settings()
        self.model = s.llm_model
        self.fast_model = s.llm_fast_model
        self.effort = s.llm_effort
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key or None, max_retries=4, timeout=900)

    def model_for(self, task: str) -> str:
        return self.model if task in MAIN_MODEL_TASKS else self.fast_model

    @staticmethod
    def _is_haiku(model: str) -> bool:
        return "haiku" in model

    def _stream(self, task: str, episode_id: int | None, effort: str | None = None, fmt: dict | None = None, **kw):
        model = self.model_for(task)
        started = time.monotonic()
        output_config = {"format": fmt} if fmt else {}
        try:
            if self._is_haiku(model):
                # Haiku 4.5：不支援 effort 與 adaptive thinking，也沒有 server-side fallback
                if output_config:
                    kw["output_config"] = output_config
                with self.client.messages.stream(model=model, max_tokens=kw.pop("max_tokens", 32000), **kw) as stream:
                    msg = stream.get_final_message()
            else:
                output_config["effort"] = effort or self.effort
                with self.client.beta.messages.stream(
                    model=model,
                    max_tokens=kw.pop("max_tokens", 64000),
                    betas=[FALLBACK_BETA],
                    extra_body={"fallbacks": "default"},
                    thinking={"type": "adaptive"},
                    output_config=output_config,
                    **kw,
                ) as stream:
                    msg = stream.get_final_message()
        except (anthropic.BadRequestError, anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise PermanentError(f"{task}: {e}") from e
        usd = costs.llm_cost(model, msg.usage)
        costs.record(episode_id, "anthropic", task, usd, model=msg.model,
                     input_tokens=msg.usage.input_tokens, output_tokens=msg.usage.output_tokens)
        log.info("llm_call", extra={"task": task, "model": model, "episode_id": episode_id, "stop_reason": msg.stop_reason,
                                    "seconds": round(time.monotonic() - started, 1), "message_id": msg.id})
        if msg.stop_reason == "refusal":
            raise PermanentError(f"{task}: model refused ({getattr(msg, 'stop_details', None)})")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError(f"{task}: output truncated at max_tokens")
        return msg

    @staticmethod
    def _text(msg) -> str:
        return "".join(b.text for b in msg.content if b.type == "text")

    def json(self, task: str, system: str, prompt: str, schema: dict, episode_id: int | None = None,
             effort: str | None = None) -> Any:
        msg = self._stream(
            task, episode_id,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            effort=effort, fmt={"type": "json_schema", "schema": schema},
        )
        return json.loads(self._text(msg))

    def vision_json(self, task: str, system: str, prompt: str, images: list, schema: dict, episode_id: int | None = None) -> Any:
        import base64

        content = []
        for img in images:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                         "data": base64.standard_b64encode(img.read_bytes()).decode()}})
        content.append({"type": "text", "text": prompt})
        msg = self._stream(task, episode_id, system=system, messages=[{"role": "user", "content": content}],
                           effort="medium", fmt={"type": "json_schema", "schema": schema})
        return json.loads(self._text(msg))

    def text(self, task: str, system: str, prompt: str, episode_id: int | None = None, effort: str | None = None) -> str:
        msg = self._stream(task, episode_id, effort=effort, system=system, messages=[{"role": "user", "content": prompt}])
        return self._text(msg)

    def research(self, task: str, system: str, prompt: str, episode_id: int | None = None,
                 max_searches: int = 25) -> ResearchResult:
        messages: list[dict] = [{"role": "user", "content": prompt}]
        tool_type = "web_search_20250305" if self._is_haiku(self.model_for(task)) else "web_search_20260209"
        tools = [{"type": tool_type, "name": "web_search", "max_uses": max_searches}]
        content: list = []
        for _ in range(MAX_PAUSE_CONTINUATIONS):
            msg = self._stream(task, episode_id, system=system, messages=messages, tools=tools)
            content.extend(msg.content)
            if msg.stop_reason != "pause_turn":
                break
            messages = [{"role": "user", "content": prompt}, {"role": "assistant", "content": msg.content}]
        sources: dict[str, dict] = {}
        for b in content:
            if b.type == "web_search_tool_result" and isinstance(b.content, list):
                for r in b.content:
                    if getattr(r, "url", None):
                        sources.setdefault(r.url, {"url": r.url, "title": getattr(r, "title", ""),
                                                   "page_age": getattr(r, "page_age", "") or ""})
            if b.type == "text":
                for c in getattr(b, "citations", None) or []:
                    url = getattr(c, "url", None)
                    if url:
                        sources.setdefault(url, {"url": url, "title": getattr(c, "title", ""), "page_age": ""})
        return ResearchResult(text=self._text_from(content), sources=list(sources.values()))

    @staticmethod
    def _text_from(content) -> str:
        return "".join(b.text for b in content if b.type == "text")
