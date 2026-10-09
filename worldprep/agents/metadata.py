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
           "description": description, "tags": tags,
           "keywords": meta["keywords"], "hashtags": meta["hashtags"], "pinned_comment": meta["pinned_comment"],
           "chapters": [{"t": t, "label": h} for t, h in chs], "slogan": SLOGAN, "thesis": script["thesis"]}
    st.write_json(episode_id, "final", "metadata.json", out)
    with session() as s:
        s.get(Episode, episode_id).title = out["title"]
    return out
