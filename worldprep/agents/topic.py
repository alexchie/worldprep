from datetime import datetime, timezone

import yaml
from sqlalchemy import select

from ..config import ROOT
from ..db import audit, next_episode_number, session
from ..logging_setup import log
from ..models import Episode, Topic
from .prompts import EDITORIAL_DNA

SCHEMA = {
    "type": "object",
    "properties": {
        "choice_index": {"type": "integer"},
        "angle": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["choice_index", "angle", "reason"],
    "additionalProperties": False,
}


def seed_topics() -> None:
    items = yaml.safe_load((ROOT / "config" / "destinations.yaml").read_text(encoding="utf-8"))
    with session() as s:
        existing = set(s.scalars(select(Topic.destination)))
        for it in items:
            if it["destination"] not in existing:
                s.add(Topic(destination=it["destination"], region=it["region"], angle=it.get("angle", "")))


def select_topic(p, destination: str | None = None) -> int:
    seed_topics()
    with session() as s:
        unused = list(s.scalars(select(Topic).where(Topic.used_at.is_(None)).order_by(Topic.id)))
        recent = list(s.scalars(select(Episode).order_by(Episode.episode_number.desc()).limit(6)))
        if destination:
            topic = s.scalar(select(Topic).where(Topic.destination == destination))
            if topic is None:
                topic = Topic(destination=destination, region="自訂", angle="")
                s.add(topic)
                s.flush()
            angle, reason = topic.angle, "manual"
        else:
            if not unused:
                raise RuntimeError("選題池已用完，請在 config/destinations.yaml 新增目的地")
            listing = "\n".join(f"{i}. {t.destination}（{t.region}）— 參考切角：{t.angle}" for i, t in enumerate(unused))
            history = "、".join(f"{e.destination}（{e.region}）" for e in recent) or "（尚無）"
            prompt = (
                f"近期已製作：{history}\n\n可選目的地：\n{listing}\n\n"
                "請選出下一集最適合的目的地。考量：強烈的歷史故事、有趣的城市發展、重要的商業/經濟故事、"
                "鮮明文化、可辨識的景點、視覺潛力、目前觀眾興趣。避免與近期集數地區重複，讓頻道在全球城市、亞洲、歐洲、北美、新興城市、國家與地區間輪替。\n"
                "angle 請寫成一個能貫穿「歷史→城市→商業→文化→景點」的核心大問題（繁體中文，一句話）。"
            )
            r = p.llm.json("topic", EDITORIAL_DNA, prompt, SCHEMA, effort="medium")
            idx = min(max(0, int(r["choice_index"])), len(unused) - 1)
            topic, angle, reason = unused[idx], r["angle"], r["reason"]
        ep = Episode(episode_number=next_episode_number(s), destination=topic.destination, region=topic.region,
                     topic=angle or topic.angle)
        s.add(ep)
        s.flush()
        topic.used_episode_id, topic.used_at = ep.id, datetime.now(timezone.utc)
        audit(s, "topic_selected", ep.id, destination=topic.destination, angle=ep.topic, reason=reason)
        log.info("topic_selected", extra={"episode_id": ep.id, "destination": topic.destination, "ep": ep.episode_number})
        return ep.id


REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "destination": {"type": "string"},
        "region": {"type": "string"},
        "angle": {"type": "string"},
    },
    "required": ["destination", "region", "angle"],
    "additionalProperties": False,
}


def select_topic_from_request(p, request_id: int | None, text: str) -> int:
    """依頻道主指定的主題（email 回覆或手動指定目的地）建立新集數。"""
    from ..models import TopicRequest

    r = p.llm.json(
        "topic_request", EDITORIAL_DNA,
        f"頻道主指定的下一集主題：「{text}」\n\n"
        "destination：主要的城市、國家或地區名稱（繁體中文，例如「京都」）。"
        "region：從 亞洲城市、歐洲城市、北美城市、新興城市、國家、地區、歐亞交界、大洋洲城市 中選一個。"
        "angle：一個能貫穿「歷史→城市→商業→文化→景點」的核心大問題（繁體中文，一句話）；"
        "若頻道主已寫出角度或問題，保留其原意並潤飾即可，不要改變主題。",
        REQUEST_SCHEMA,
    )
    seed_topics()
    with session() as s:
        topic = s.scalar(select(Topic).where(Topic.destination == r["destination"]))
        if topic is None:
            topic = Topic(destination=r["destination"], region=r["region"], angle=r["angle"])
            s.add(topic)
        ep = Episode(episode_number=next_episode_number(s), destination=r["destination"], region=r["region"],
                     topic=r["angle"], requested_topic=text)
        s.add(ep)
        s.flush()
        topic.used_episode_id, topic.used_at = ep.id, datetime.now(timezone.utc)
        if request_id is not None:
            s.get(TopicRequest, request_id).used_episode_id = ep.id
        audit(s, "topic_from_email", ep.id, request=text, destination=r["destination"], angle=r["angle"])
        log.info("topic_from_email", extra={"episode_id": ep.id, "destination": r["destination"]})
        return ep.id
