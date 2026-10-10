from datetime import datetime, timezone

import yaml
from sqlalchemy import select

from ..brand import format_title, validate_title
from ..config import ROOT
from ..db import audit, next_episode_number, session
from ..logging_setup import log
from ..models import Episode, Topic
from ..storage import get_storage
from .prompts import EDITORIAL_DNA

TITLE_STYLE_DIR = ROOT / "video_title"
ARCHETYPES = ["A", "B", "C", "D", "E", "F", "G"]

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

TITLES_PROP = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"main_title": {"type": "string"}, "archetype": {"type": "string", "enum": ARCHETYPES}},
        "required": ["main_title", "archetype"],
        "additionalProperties": False,
    },
}

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "destination": {"type": "string"},
        "region": {"type": "string"},
        "familiar_phenomenon": {"type": "string"},
        "archetype": {"type": "string", "enum": ARCHETYPES},
        "core_question": {"type": "string"},
        "titles": TITLES_PROP,
        "narrative_arc": {"type": "string"},
        "opening_15s": {"type": "string"},
        "research_questions": {"type": "array", "items": {"type": "string"}},
        "wow_details_to_verify": {"type": "array", "items": {"type": "string"}},
        "script_direction": {"type": "string"},
        "visual_direction": {"type": "string"},
        "search_keywords_en": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["destination", "region", "familiar_phenomenon", "archetype", "core_question", "titles", "narrative_arc",
                 "opening_15s", "research_questions", "wow_details_to_verify", "script_direction", "visual_direction",
                 "search_keywords_en"],
    "additionalProperties": False,
}

PRODUCER_ROLE = """你是本集的專案負責人（製作人）。你先決定這一集要回答什麼問題、標題怎麼下，再把工作分派給研究、查核、編劇、分鏡、縮圖等同事。
同事不會讀到風格指南，他們只看得到你寫的工作說明，所以說明要具體、可執行。

規則：
- 嚴格依照下方「標題與內容風格指南」：從觀眾熟悉的現象切入，標題屬於七種原型（A–G）之一，腳本走該原型的敘事弧線。
- titles：12 個候選主標題（之後由總編輯挑 3 個），至少涵蓋 4 種原型。不要包含「｜世界先修課 EP.xx」（系統會自動加上），每個 28 字以內。
  標題的第一眼吸引力來自「觀眾認得、而且覺得怪」的具體東西，不是抽象概念：
  · 鉤子必須是具體的物件、現象、人物、數字或反差（例如「街上跑的是美軍吉普車」「滿街西班牙姓氏」「一張紙統治世界」），
    最好直接取自 familiar_phenomenon，讓人看到就想問「對耶，為什麼？」。
  · 禁止用抽象詞當鉤子：樞紐、交換站、面貌、發展、連結、歷史脈絡、城市魅力、轉變之路。
  · 12 個要真的不一樣：換不同的切入物件與句型（為什麼／憑什麼／你以為…其實／從…到…／數字開頭／第二人稱），不要同一句話換幾個字。
  · 範本標題只學套路，不可照抄或只替換名詞。
  此時還沒研究，標題裡的數字或專有事實必須是你有把握、且研究時會被查核的；之後若查核不支持，審查階段會改標題。
- region：從 亞洲城市、歐洲城市、北美城市、新興城市、國家、地區、歐亞交界、大洋洲城市 中選一個。
- 本集只講一個有趣的故事（約 5–7 分鐘），不要面面俱到、不要講太深；五個面向只在故事需要時帶到。
- research_questions：研究員要回答的 3–5 個具體問題，都要直接支撐這個故事與標題的承諾。
- search_keywords_en：2–3 組英文關鍵字（用來查英文維基百科），例如「Kyoto kimono」「Nishijin weaving」。
- wow_details_to_verify：3–5 個可能讓觀眾說「真的假的？」的細節，交給研究與查核去證實或推翻。
- script_direction：給編劇的指示（敘事弧線怎麼落到各段、每個標題承諾要在哪裡兌現、結尾如何回到對旅人的意義）。
- visual_direction：給分鏡與縮圖的畫面方向（主視覺、一定要出現的地點或物件）。"""


def title_style() -> str:
    """頻道主提供的標題風格指南與範本標題（video_title/ 底下的 .md），每次規劃都會重新讀取。"""
    files = sorted(TITLE_STYLE_DIR.glob("*.md"), key=lambda f: f.name != "style_guide.md")
    return "\n\n".join(f.read_text(encoding="utf-8").strip() for f in files)


def producer_system() -> str:
    return f"{EDITORIAL_DNA}\n\n{PRODUCER_ROLE}\n\n{title_style()}"


def _recent() -> str:
    with session() as s:
        recent = list(s.scalars(select(Episode).order_by(Episode.episode_number.desc()).limit(6)))
        return "、".join(f"{e.destination}（{e.title or e.topic}）" for e in recent) or "（尚無）"


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"main_title": {"type": "string"}, "archetype": {"type": "string", "enum": ARCHETYPES},
                               "reason": {"type": "string"}},
                "required": ["main_title", "archetype", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}


def judge_titles(p, brief: dict, candidates: list[dict], episode_id: int | None = None) -> list[dict]:
    """總編輯：站在滑手機的觀眾角度，從候選中挑出第一眼最想點的 3 個（可潤飾字句，不可改變事實）。"""
    ref = (TITLE_STYLE_DIR / "reference_titles.md")
    listing = "\n".join(f"- [{c['archetype']}] {c['main_title']}" for c in candidates)
    r = p.llm.json(
        "title_judge",
        f"{EDITORIAL_DNA}\n\n你是頻道總編輯，負責挑標題。你的標準是：一個在 YouTube 首頁滑過去、對這個地方幾乎不了解的華語觀眾，"
        "第一眼看到會不會停下來、覺得「這很有趣，我想知道答案」。下面是頻道主提供、實際表現很好的範本標題，請用同樣的嗅覺判斷。\n\n"
        + (ref.read_text(encoding="utf-8") if ref.exists() else ""),
        f"本集：{brief['destination']}\n觀眾熟悉的現象：{brief['familiar_phenomenon']}\n核心問題：{brief['core_question']}\n\n"
        f"## 候選標題\n{listing}\n\n"
        "挑出 3 個（最好的放第一），三個要是不同的切入點與原型。評判重點依序：\n"
        "1. 鉤子是否具體、讓人覺得「怪」（物件、現象、人物、數字、反差），而不是抽象詞（樞紐、交換站、面貌、發展）；\n"
        "2. 前 15 字內就看得懂、看得到鉤子（手機會截斷）；\n"
        "3. 有沒有範本那種「我也好奇過」的感覺；\n"
        "4. 影片能兌現，不誇大。\n"
        "可以潤飾字句讓它更有力（28 字內，不含「｜世界先修課 EP.xx」），但不可加入候選中沒有的事實或數字。"
        "如果候選都不夠好，可以根據觀眾熟悉的現象改寫出更好的版本。reason 用一句話說明為什麼會想點。",
        JUDGE_SCHEMA, episode_id,
    )
    return [{"main_title": x["main_title"], "archetype": x["archetype"]} for x in r["picks"][:3]] or candidates[:3]


def plan(p, request: str) -> dict:
    """製作人：依指定主題產出本集企劃（核心問題、原型、12 個候選標題、各同事的工作說明），再由總編輯挑出 3 個標題。"""
    brief = p.llm.json(
        "topic_request", producer_system(),
        f"頻道主指定的下一集主題：「{request}」\n近期已製作：{_recent()}\n\n"
        "若頻道主已寫出角度或問題，保留其原意，不要改變主題。請寫出本集企劃。",
        BRIEF_SCHEMA,
    )
    brief["title_pool"] = brief["titles"]
    brief["titles"] = judge_titles(p, brief, brief["title_pool"])
    return brief


def full_titles(brief: dict, n: int) -> list[str]:
    """把企劃中的主標題加上「｜世界先修課 EP.xx」，不合規的排到最後。"""
    full = [format_title(t["main_title"], n) for t in brief["titles"]]
    return sorted(full, key=lambda t: bool(validate_title(t, n)))


def read_brief(episode_id: int) -> dict | None:
    st = get_storage()
    return st.read_json(episode_id, "plan", "brief.json") if st.exists(episode_id, "plan", "brief.json") else None


def brief_for(episode_id: int, role: str) -> str:
    """給下游同事看的工作說明（只放該角色需要的部分，控制 token）。"""
    b = read_brief(episode_id)
    if not b:
        return ""
    head = f"## 製作人的企劃\n標題：{b['titles'][0]['main_title']}\n原型：{b['archetype']}\n觀眾熟悉的現象：{b['familiar_phenomenon']}\n核心問題：{b['core_question']}\n"
    if role == "research":
        return head + "要回答的問題：\n- " + "\n- ".join(b["research_questions"]) + "\n需要證實或推翻的細節：\n- " + "\n- ".join(b["wow_details_to_verify"])
    if role == "script":
        return (head + f"敘事弧線：{b['narrative_arc']}\n開頭 hook：{b['opening_15s']}\n編劇指示：{b['script_direction']}\n"
                "可能的「真的假的」細節（只能用已查核的版本）：\n- " + "\n- ".join(b["wow_details_to_verify"]))
    if role == "review":
        return head + f"敘事弧線：{b['narrative_arc']}\n開頭 hook：{b['opening_15s']}"
    if role == "visual":
        return head + f"畫面方向：{b['visual_direction']}"
    return head


def _save(episode_id: int, brief: dict) -> None:
    get_storage().write_json(episode_id, "plan", "brief.json", brief)


def set_title(episode_id: int, main_title: str, archetype: str, reason: str) -> None:
    """審查發現標題承諾沒有被查核事實支持時，換上修正版標題（原標題只留在修訂紀錄，不再當備選）。"""
    b = read_brief(episode_id)
    old = b["titles"][0]["main_title"]
    b["titles"][0] = {"main_title": main_title, "archetype": archetype}
    b["title_revisions"] = b.get("title_revisions", []) + [{"from": old, "to": main_title, "reason": reason}]
    _save(episode_id, b)
    with session() as s:
        ep = s.get(Episode, episode_id)
        ep.title = full_titles(b, ep.episode_number)[0]
        audit(s, "title_revised", episode_id, title=main_title, reason=reason[:500])


RETITLE_SCHEMA = {"type": "object", "properties": {"titles": TITLES_PROP}, "required": ["titles"], "additionalProperties": False}


def retitle(p, episode_id: int, feedback: str = "") -> None:
    """重做標題：製作人依完成的腳本與頻道主回饋重新下 3 個標題。"""
    st = get_storage()
    b = read_brief(episode_id)
    script = st.path(episode_id, "scripts", "script.txt").read_text(encoding="utf-8")
    r = p.llm.json(
        "topic_request", producer_system(),
        f"本集原企劃：核心問題「{b['core_question']}」，原型 {b['archetype']}，原標題「{b['titles'][0]['main_title']}」。\n"
        f"影片已完成，請依腳本重新下 12 個候選標題（只能承諾腳本有兌現的內容）。"
        + (f"\n頻道主回饋：{feedback}" if feedback else "") + f"\n\n## 腳本\n{script}",
        RETITLE_SCHEMA, episode_id,
    )
    b["title_pool"] = r["titles"]
    b["titles"] = judge_titles(p, b, r["titles"], episode_id)
    _save(episode_id, b)


def _create(brief: dict, request: str | None, request_id: int | None) -> int:
    from ..models import TopicRequest

    seed_topics()
    with session() as s:
        topic = s.scalar(select(Topic).where(Topic.destination == brief["destination"]))
        if topic is None:
            topic = Topic(destination=brief["destination"], region=brief["region"], angle=brief["core_question"])
            s.add(topic)
        n = next_episode_number(s)
        ep = Episode(episode_number=n, destination=brief["destination"], region=brief["region"],
                     topic=brief["core_question"], requested_topic=request, title=full_titles(brief, n)[0])
        s.add(ep)
        s.flush()
        topic.used_episode_id, topic.used_at = ep.id, datetime.now(timezone.utc)
        if request_id is not None:
            s.get(TopicRequest, request_id).used_episode_id = ep.id
        audit(s, "episode_planned", ep.id, request=request, destination=brief["destination"],
              question=brief["core_question"], title=ep.title)
        eid = ep.id
    _save(eid, brief)
    log.info("episode_planned", extra={"episode_id": eid, "destination": brief["destination"]})
    return eid


def seed_topics() -> None:
    items = yaml.safe_load((ROOT / "config" / "destinations.yaml").read_text(encoding="utf-8"))
    with session() as s:
        existing = set(s.scalars(select(Topic.destination)))
        for it in items:
            if it["destination"] not in existing:
                s.add(Topic(destination=it["destination"], region=it["region"], angle=it.get("angle", "")))


def select_topic(p, destination: str | None = None) -> int:
    """選題池（TOPIC_FALLBACK=auto 或手動指定目的地）：先挑目的地，再交給製作人寫企劃。"""
    seed_topics()
    if destination:
        return _create(plan(p, destination), None, None)
    with session() as s:
        unused = [(t.destination, t.region, t.angle) for t in s.scalars(select(Topic).where(Topic.used_at.is_(None)).order_by(Topic.id))]
    if not unused:
        raise RuntimeError("選題池已用完，請在 config/destinations.yaml 新增目的地")
    listing = "\n".join(f"{i}. {d}（{r}）— 參考切角：{a}" for i, (d, r, a) in enumerate(unused))
    r = p.llm.json(
        "topic", EDITORIAL_DNA,
        f"近期已製作：{_recent()}\n\n可選目的地：\n{listing}\n\n"
        "請選出下一集最適合的目的地。考量：強烈的歷史故事、有趣的城市發展、重要的商業/經濟故事、鮮明文化、可辨識的景點、視覺潛力。"
        "避免與近期集數地區重複。angle 寫成一句核心問題。",
        SCHEMA, effort="medium",
    )
    d, _, _ = unused[min(max(0, int(r["choice_index"])), len(unused) - 1)]
    return _create(plan(p, f"{d}：{r['angle']}"), None, None)


def select_topic_from_request(p, request_id: int | None, text: str) -> int:
    """依頻道主指定的主題（email 回覆或手動指定目的地）建立新集數。"""
    return _create(plan(p, text), text, request_id)
