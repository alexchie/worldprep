from sqlalchemy import select

from ..brand import CHANNEL_DESCRIPTION, SLOGAN, TITLE_SUFFIX_RE, validate_title
from ..db import session
from ..models import Asset, Episode, ResearchSource
from ..storage import get_storage
from .prompts import EDITORIAL_DNA
from .topic import full_titles, read_brief

META_SCHEMA = {
    "type": "object",
    "properties": {
        "description_intro": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "pinned_comment": {"type": "string"},
    },
    "required": ["description_intro", "tags", "keywords", "hashtags", "pinned_comment"],
    "additionalProperties": False,
}


SOCIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "shorts_title": {"type": "string"},
        "shorts_description": {"type": "string"},
        "instagram_caption": {"type": "string"},
        "threads_post": {"type": "string"},
    },
    "required": ["shorts_title", "shorts_description", "instagram_caption", "threads_post"],
    "additionalProperties": False,
}
LINK = "【YouTube 正片連結】"


def social_copy(p, episode_id: int, title: str, dest: str) -> dict:
    """三種導流文案（YouTube Shorts、IG 封面貼文、Threads），目的都是把人帶到 YouTube 正片。"""
    st = get_storage()
    hook = " ".join(sc["text"] for sc in st.read_json(episode_id, "video", "timeline.json")["scenes"] if sc["section"] == "hook")
    return p.llm.json(
        "social", f"{EDITORIAL_DNA}\n\n你是頻道的社群小編，擅長寫讓人忍不住點進去看完整影片的短文案。",
        f"正片標題：{title}\n目的地：{dest}\n短影音的開場旁白：{hook}\n\n"
        "寫三種宣傳文案，目的都是導流到 YouTube 正片。只能用影片裡講到的事實，不誇大、不劇透答案，留下懸念。"
        f"需要放正片網址的地方一律寫「{LINK}」，由頻道主發文時自己貼上。\n"
        "- shorts_title：YouTube Shorts 標題，40 字內，結尾加 #Shorts。\n"
        f"- shorts_description：Shorts 說明欄，2–3 句，引導看完整版，附上{LINK}與 3 個 hashtag（含 #世界先修課）。\n"
        "- instagram_caption：IG 封面圖貼文。第一行就要抓住人；3–5 行短段落、可用少量 emoji；"
        "IG 貼文的網址點不了，所以寫「完整影片連結在個人檔案」；結尾 5–8 個 hashtag（含 #世界先修課）。\n"
        f"- threads_post：Threads 貼文（會搭配 Shorts 影片），300 字內、口語、像在跟朋友分享一個冷知識，"
        f"最後一句引導去看完整影片並附上{LINK}；hashtag 最多 1 個。",
        SOCIAL_SCHEMA, episode_id, effort="low",
    )


def chapters(timeline: dict) -> list[tuple[float, str]]:
    out = [(0.0, "開場")]
    for sc in timeline["scenes"]:
        if sc["section_start"] and sc["section"] != "hook":
            out.append((sc["start"], sc["heading"]))
    cleaned = [out[0]]
    for t, h in out[1:]:
        if t - cleaned[-1][0] >= 10:
            cleaned.append((t, h))
    return cleaned if len(cleaned) >= 3 else []


def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def run(p, episode_id: int, feedback: str = "", force: bool = False) -> dict:
    st = get_storage()
    if st.exists(episode_id, "final", "metadata.json") and not force:
        return st.read_json(episode_id, "final", "metadata.json")
    script = st.read_json(episode_id, "scripts", "script.json")
    timeline = st.read_json(episode_id, "video", "timeline.json")
    with session() as s:
        ep = s.get(Episode, episode_id)
        dest, synthetic, n, current = ep.destination, ep.contains_synthetic_media, ep.episode_number, ep.title
        srcs = list(s.scalars(select(ResearchSource).where(ResearchSource.episode_id == episode_id,
                                                             ResearchSource.verdict.in_(["verified", "qualified"]),
                                                             ResearchSource.source_url != "")))
        attributions = [a for a in s.scalars(select(Asset).where(Asset.episode_id == episode_id, Asset.attribution_required))]
    brief = read_brief(episode_id)
    titles = full_titles(brief, n) if brief else [current]
    title = titles[0]
    meta = p.llm.json(
        "metadata", f"{EDITORIAL_DNA}\n\n你負責 YouTube 說明欄與 SEO。",
        f"標題：{title}\n目的地：{dest}\n論點：{script['thesis']}\n\n"
        "description_intro：2–3 段，說明本集的核心論點與觀眾會理解什麼（不是堆關鍵字）。tags：15–25 個（含中英文目的地名稱）。"
        "hashtags：3 個，含 #世界先修課。pinned_comment：一個引發討論的問題。\n\n## 腳本\n" + st.path(episode_id, "scripts", "script.txt").read_text(encoding="utf-8"),
        META_SCHEMA, episode_id, effort="low",
    )
    chs = chapters(timeline)
    parts = [meta["description_intro"].strip()]
    if chs:
        parts.append("章節\n" + "\n".join(f"{_fmt(t)} {h}" for t, h in chs))
    seen, src_lines = set(), []
    for r in srcs:
        if r.source_url not in seen:
            seen.add(r.source_url)
            src_lines.append(f"- {r.source}：{r.source_url}")
    if src_lines:
        parts.append("主要資料來源\n" + "\n".join(src_lines[:15]))
    if attributions:
        parts.append("素材授權\n" + "\n".join(f"- {a.creator} / {a.source}（{a.license}）" for a in attributions))
    if synthetic:
        parts.append("本片部分歷史場景為 AI 生成的示意重建畫面，並非真實影像。")
    parts.append(CHANNEL_DESCRIPTION)
    parts.append(" ".join(h if h.startswith("#") else f"#{h}" for h in meta["hashtags"]))
    description = "\n\n".join(parts).replace("<", "＜").replace(">", "＞")[:4900]
    tags, total = [], 0
    for tag in meta["tags"]:
        if total + len(tag) + 2 > 480:
            break
        tags.append(tag)
        total += len(tag) + 2
    alternates = [t for t in titles[1:] if t != title and not validate_title(t, n)][:2]
    out = {"title": title, "main_title": TITLE_SUFFIX_RE.sub("", title).rstrip("｜| "), "title_alternates": alternates,
           "description": description, "tags": tags, "social": social_copy(p, episode_id, title, dest),
           "keywords": meta["keywords"], "hashtags": meta["hashtags"], "pinned_comment": meta["pinned_comment"],
           "chapters": [{"t": t, "label": h} for t, h in chs], "slogan": SLOGAN, "thesis": script["thesis"]}
    st.write_json(episode_id, "final", "metadata.json", out)
    with session() as s:
        s.get(Episode, episode_id).title = out["title"]
    return out
