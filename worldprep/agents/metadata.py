from pathlib import Path

from sqlalchemy import select

from ..brand import CHANNEL_DESCRIPTION, SLOGAN, format_title, validate_title
from ..db import session
from ..models import Asset, Episode, ResearchSource
from ..storage import get_storage
from .prompts import EDITORIAL_DNA

TITLE_CRITERIA = ["curiosity", "clarity", "relevance", "historical_depth", "business_insight", "cultural_interest",
                  "travel_relevance", "thumbnail_compatibility", "accuracy", "click_appeal_without_clickbait"]

TITLE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "main_title": {"type": "string"},
                    "pattern": {"type": "string"},
                    "scores": {"type": "object", "properties": {c: {"type": "number"} for c in TITLE_CRITERIA},
                               "required": TITLE_CRITERIA, "additionalProperties": False},
                    "supported_by_video": {"type": "boolean"},
                },
                "required": ["main_title", "pattern", "scores", "supported_by_video"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

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

TITLE_STYLE_DIR = Path(__file__).resolve().parents[2] / "video_title"

TITLE_GUIDE = """標題規則：
- 依照下方「標題與內容風格指南」：標題必須屬於七種原型（A–G）之一，pattern 欄位只填原型代號；兩段式結構，鉤子放在前 20 字。
- 範本標題只用來學套路與節奏，不得照抄或只替換名詞。
- 禁止：XX旅遊攻略、XX十大景點、XX必去景點、XX旅遊介紹、XX景點推薦、XX完整介紹。禁止不誠實的標題黨，標題的每個承諾（含情緒詞、數字）都必須被影片內容支持。
- main_title 不要包含「｜世界先修課 EP.xx」，系統會自動加上，所以 main_title 控制在 30 字以內。
- 每個面向 0–10 分。"""


def title_style() -> str:
    """頻道主提供的標題風格指南與範本標題（video_title/ 底下的 .md），每次產生標題都會重新讀取。"""
    files = sorted(TITLE_STYLE_DIR.glob("*.md"), key=lambda f: f.name != "style_guide.md")
    return "\n\n".join(f.read_text(encoding="utf-8").strip() for f in files)


def _score(c: dict) -> float:
    s = c["scores"]
    brand_fit = (s["historical_depth"] + s["business_insight"] + s["cultural_interest"]) / 3
    return (s["curiosity"] * s["relevance"] * s["accuracy"] * brand_fit) ** 0.25 + 0.1 * (s["clarity"] + s["click_appeal_without_clickbait"] + s["thumbnail_compatibility"])


def generate_title(p, episode_id: int, feedback: str = "") -> dict:
    st = get_storage()
    script = st.read_json(episode_id, "scripts", "script.json")
    with session() as s:
        ep = s.get(Episode, episode_id)
        dest, n = ep.destination, ep.episode_number
    prompt = (f"目的地：{dest}\n本集論點：{script['thesis']}\n\n## 腳本\n" + st.path(episode_id, "scripts", "script.txt").read_text(encoding="utf-8")
              + "\n\n產生至少 10 個候選主標題，至少涵蓋 2 種原型，並逐一評分。" + (f"\n\n製作人回饋：{feedback}" if feedback else ""))
    data = p.llm.json("titles", f"{EDITORIAL_DNA}\n\n你是頻道的標題策略師。\n{TITLE_GUIDE}\n\n{title_style()}", prompt, TITLE_SCHEMA,
                      episode_id, effort="medium")
    ranked = []
    for c in data["candidates"]:
        full = format_title(c["main_title"], n)
        c["full_title"], c["total"], c["errors"] = full, round(_score(c), 3), validate_title(full, n)
        ranked.append(c)
    ranked.sort(key=lambda c: (not c["errors"] and c["supported_by_video"], c["total"]), reverse=True)
    st.write_json(episode_id, "final", "title_candidates.json", ranked)
    return ranked[0]


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
    best = generate_title(p, episode_id, feedback)
    script = st.read_json(episode_id, "scripts", "script.json")
    timeline = st.read_json(episode_id, "video", "timeline.json")
    with session() as s:
        ep = s.get(Episode, episode_id)
        dest, synthetic = ep.destination, ep.contains_synthetic_media
        srcs = list(s.scalars(select(ResearchSource).where(ResearchSource.episode_id == episode_id,
                                                             ResearchSource.verdict.in_(["verified", "qualified"]),
                                                             ResearchSource.source_url != "")))
        attributions = [a for a in s.scalars(select(Asset).where(Asset.episode_id == episode_id, Asset.attribution_required))]
    meta = p.llm.json(
        "metadata", f"{EDITORIAL_DNA}\n\n你負責 YouTube 說明欄與 SEO。",
        f"標題：{best['full_title']}\n目的地：{dest}\n論點：{script['thesis']}\n\n"
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
    alternates = [c["full_title"] for c in st.read_json(episode_id, "final", "title_candidates.json")[1:]
                  if not c["errors"] and c["supported_by_video"]][:2]
    out = {"title": best["full_title"], "main_title": best["main_title"], "title_alternates": alternates, "description": description, "tags": tags,
           "keywords": meta["keywords"], "hashtags": meta["hashtags"], "pinned_comment": meta["pinned_comment"],
           "chapters": [{"t": t, "label": h} for t, h in chs], "slogan": SLOGAN, "thesis": script["thesis"]}
    st.write_json(episode_id, "final", "metadata.json", out)
    with session() as s:
        s.get(Episode, episode_id).title = out["title"]
    return out
