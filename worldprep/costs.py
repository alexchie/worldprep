from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .config import get_settings
from .db import session
from .logging_setup import log
from .models import Cost

# USD per 1M tokens (input, output, cache_read)
LLM_PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}
WEB_SEARCH_PER_1K = 10.0
TTS_PER_1M_CHARS = {"azure": 16.0, "edge": 0.0, "mock": 0.0}
IMAGE_PER_IMAGE = {"openai": 0.08, "pexels": 0.0, "card": 0.0, "mock": 0.0}


class BudgetExceeded(Exception):
    pass


def llm_cost(model: str, usage, discount: float = 1.0) -> float:
    """discount=0.5 為 Batch API 半價（只折 token 費，不折網路搜尋費）。快取寫入：5 分鐘 1.25 倍、1 小時 2 倍。"""
    pin, pout, pcache = LLM_PRICES.get(model, LLM_PRICES["claude-opus-5-5"])
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cc = getattr(usage, "cache_creation", None)
    cw_1h = (getattr(cc, "ephemeral_1h_input_tokens", 0) or 0) if cc else 0
    stu = getattr(usage, "server_tool_use", None)
    searches = (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
    tokens = (inp * pin + (cw - cw_1h) * pin * 1.25 + cw_1h * pin * 2 + cr * pcache + out * pout) / 1e6
    return tokens * discount + searches * WEB_SEARCH_PER_1K / 1000


def record(episode_id: int | None, provider: str, service: str, actual: float, estimated: float | None = None, **detail) -> None:
    with session() as s:
        s.add(Cost(episode_id=episode_id, provider=provider, service=service,
                   estimated_cost=estimated if estimated is not None else actual, actual_cost=actual, detail=detail or None))
    log.info("cost", extra={"episode_id": episode_id, "provider": provider, "service": service, "usd": round(actual, 4)})


def episode_total(episode_id: int) -> float:
    with session() as s:
        return float(s.scalar(select(func.coalesce(func.sum(Cost.actual_cost), 0)).where(Cost.episode_id == episode_id)))


def today_total() -> float:
    tz = ZoneInfo(get_settings().timezone)
    start = datetime.combine(datetime.now(tz).date(), time.min, tz).astimezone(timezone.utc)
    with session() as s:
        return float(s.scalar(select(func.coalesce(func.sum(Cost.actual_cost), 0)).where(Cost.timestamp >= start)))


def check_budget(episode_id: int, upcoming: float = 0.0) -> bool:
    """回傳 True 表示仍在預算內；False 表示應降級非必要生成。"""
    s = get_settings()
    ep, day = episode_total(episode_id), today_total()
    ok = ep + upcoming <= s.episode_budget_usd and day + upcoming <= s.daily_budget_usd
    if not ok:
        log.warning("budget_exceeded", extra={"episode_id": episode_id, "episode_usd": ep, "daily_usd": day, "upcoming": upcoming})
    return ok
