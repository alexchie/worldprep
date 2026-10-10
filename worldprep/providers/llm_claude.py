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

# 每個工作用哪一級模型：opus（關鍵判斷）、sonnet（理解與撰寫）、haiku（抽取與清單檢查）
TASK_TIER = {
    "topic": "sonnet",
    "topic_request": "sonnet",  # 製作人：讀風格指南、定核心問題與候選標題、寫各同事的工作說明
    "title_judge": "opus",  # 總編輯：從候選中挑第一眼最有趣的 3 個標題
    "research": "sonnet",
    "research_extract": "haiku",
    "factcheck": "opus",
    "factcheck_extract": "haiku",
    "script": "opus",
    "script_review": "opus",
    "script_revise": "sonnet",
    "storyboard": "sonnet",
    "metadata": "haiku",
    "social": "sonnet",  # 三種導流文案：Shorts、IG、Threads
    "thumbnail": "sonnet",
    "qa_vision": "haiku",
    "cover_check": "sonnet",
    "cover_check_strict": "opus",  # 英文封面第二道校對
    "slide_check": "haiku",
    "qa_vision_escalate": "opus",
}
# 需要即時結果的工作不走 Batch；其餘在夜間排程中改用 Batch API（半價）
SYNC_TASKS = {"research", "topic", "topic_request"}


class ClaudeProvider:
    """LLMProvider + ResearchProvider（web_search 由 Anthropic 伺服器端執行）。"""

    def __init__(self):
        s = get_settings()
        self.models = {"opus": s.llm_model, "sonnet": s.llm_sonnet_model, "haiku": s.llm_fast_model}
        self.effort = s.llm_effort
        self.batch = s.batch_enabled
        self.batch_wait = s.batch_wait_minutes * 60
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key or None, max_retries=4, timeout=900)

    def model_for(self, task: str, episode_id: int | None = None) -> str:
        """依工作分配模型；本集 Claude 花費接近上限時自動降級（6 成：Opus→Sonnet；8 成 5：一律 Haiku）。"""
        tier = TASK_TIER.get(task, "sonnet")
        if episode_id is not None:
            spent, cap = costs.provider_total(episode_id, "anthropic"), get_settings().claude_episode_budget_usd
            if spent >= cap * 0.85:
                tier = "haiku"
            elif spent >= cap * 0.6 and tier == "opus":
                tier = "sonnet"
            if tier != TASK_TIER.get(task, "sonnet"):
                log.info("claude_downgraded", extra={"task": task, "tier": tier, "spent": round(spent, 3)})
        return self.models[tier]

    @staticmethod
    def _is_haiku(model: str) -> bool:
        return "haiku" in model

    def _params(self, model: str, effort: str | None, fmt: dict | None, **kw) -> dict:
        params = {"model": model, "max_tokens": 32000 if self._is_haiku(model) else 64000, **kw}
        output_config = {"format": fmt} if fmt else {}
        if not self._is_haiku(model):
            # Haiku 4.5 不支援 effort 與 adaptive thinking
            params["thinking"] = {"type": "adaptive"}
            output_config["effort"] = effort or self.effort
        if output_config:
            params["output_config"] = output_config
        return params

    def _sync(self, params: dict):
        if self._is_haiku(params["model"]):
            with self.client.messages.stream(**params) as stream:
                return stream.get_final_message()
        with self.client.beta.messages.stream(**params, betas=[FALLBACK_BETA], extra_body={"fallbacks": "default"}) as stream:
            return stream.get_final_message()

    def _batched(self, task: str, params: dict):
        """單筆 Batch 請求：半價；超過等待上限就取消並改為即時呼叫，避免拖過交付時間。"""
        batch = self.client.messages.batches.create(requests=[{"custom_id": task[:64], "params": params}])
        deadline = time.monotonic() + self.batch_wait
        while time.monotonic() < deadline:
            time.sleep(15)
            if self.client.messages.batches.retrieve(batch.id).processing_status == "ended":
                for r in self.client.messages.batches.results(batch.id):
                    if r.result.type == "succeeded":
                        return r.result.message
                    err = getattr(r.result, "error", None)
                    err = getattr(err, "error", err)
                    if r.result.type == "errored" and "invalid_request" in str(getattr(err, "type", "")):
                        raise PermanentError(f"{task}: batch invalid request: {getattr(err, 'message', err)}")
                    log.warning("batch_result_not_succeeded", extra={"task": task, "type": r.result.type})
                return None
        self.client.messages.batches.cancel(batch.id)
        log.warning("batch_timeout_fallback_sync", extra={"task": task, "batch_id": batch.id})
        return None

    def _call(self, task: str, episode_id: int | None, effort: str | None = None, fmt: dict | None = None, **kw):
        model = self.model_for(task, episode_id)
        params = self._params(model, effort, fmt, **kw)
        started = time.monotonic()
        discount = 1.0
        msg = None
        try:
            if self.batch and task not in SYNC_TASKS:
                msg = self._batched(task, params)
                discount = 0.5 if msg is not None else 1.0
            if msg is None:
                msg = self._sync(params)
        except (anthropic.BadRequestError, anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise PermanentError(f"{task}: {e}") from e
        u = msg.usage
        usd = costs.llm_cost(model, u, discount)
        costs.record(episode_id, "anthropic", task, usd, model=msg.model, batch=discount < 1,
                     input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                     cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                     cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0)
        log.info("llm_call", extra={"task": task, "model": model, "batch": discount < 1, "episode_id": episode_id,
                                    "stop_reason": msg.stop_reason, "seconds": round(time.monotonic() - started, 1),
                                    "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0})
        if msg.stop_reason == "refusal":
            raise PermanentError(f"{task}: model refused ({getattr(msg, 'stop_details', None)})")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError(f"{task}: output truncated at max_tokens")
        return msg

    @staticmethod
    def _text(msg) -> str:
        return "".join(b.text for b in msg.content if b.type == "text")

    def _user(self, task: str, prompt: str, cached_prefix: str | None, episode_id: int | None = None) -> list[dict]:
        """cached_prefix：同一模型會重複送出的大段內容（例如已查核事實），放最前面並設 1 小時快取。"""
        if not cached_prefix or self._is_haiku(self.model_for(task, episode_id)):
            return [{"role": "user", "content": (cached_prefix + "\n\n" if cached_prefix else "") + prompt}]
        return [{"role": "user", "content": [
            {"type": "text", "text": cached_prefix, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            {"type": "text", "text": prompt},
        ]}]

    def json(self, task: str, system: str, prompt: str, schema: dict, episode_id: int | None = None,
             effort: str | None = None, cached_prefix: str | None = None) -> Any:
        msg = self._call(task, episode_id, effort=effort, fmt={"type": "json_schema", "schema": schema},
                         system=system, messages=self._user(task, prompt, cached_prefix, episode_id))
        return json.loads(self._text(msg))

    def vision_json(self, task: str, system: str, prompt: str, images: list, schema: dict, episode_id: int | None = None) -> Any:
        import base64

        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.standard_b64encode(img.read_bytes()).decode()}}
                   for img in images]
        content.append({"type": "text", "text": prompt})
        msg = self._call(task, episode_id, effort="medium", fmt={"type": "json_schema", "schema": schema},
                         system=system, messages=[{"role": "user", "content": content}])
        return json.loads(self._text(msg))

    def text(self, task: str, system: str, prompt: str, episode_id: int | None = None, effort: str | None = None) -> str:
        return self._text(self._call(task, episode_id, effort=effort, system=system,
                                     messages=[{"role": "user", "content": prompt}]))

    def research(self, task: str, system: str, prompt: str, episode_id: int | None = None,
                 max_searches: int | None = None) -> ResearchResult:
        model = self.model_for(task, episode_id)
        max_searches = max_searches or get_settings().research_max_searches
        tool_type = "web_search_20250305" if self._is_haiku(model) else "web_search_20260209"
        tools = [{"type": tool_type, "name": "web_search", "max_uses": max_searches}]
        # 搜尋達到伺服器迴圈上限（pause_turn）時會帶著同樣的前文續傳，自動快取讓續傳只付快取價
        extra = {} if self._is_haiku(model) else {"cache_control": {"type": "ephemeral"}}
        messages: list[dict] = [{"role": "user", "content": prompt}]
        content: list = []
        for _ in range(MAX_PAUSE_CONTINUATIONS):
            msg = self._call(task, episode_id, system=system, messages=messages, tools=tools, **extra)
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
        return ResearchResult(text="".join(b.text for b in content if b.type == "text"), sources=list(sources.values()))
