from sqlalchemy import delete

from ..config import get_settings
from ..db import session
from ..models import Episode, ResearchSource
from ..storage import get_storage
from .prompts import EDITORIAL_DNA, SOURCE_POLICY
from ..providers import freesources
from .topic import brief_for, read_brief

SECTIONS = ["geography", "history", "city", "business", "culture", "attractions"]

CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "section": {"type": "string", "enum": SECTIONS},
                    "importance": {"type": "string", "enum": ["key", "supporting"]},
                    "source": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_type": {"type": "string", "enum": ["government", "official_statistics", "academic",
                                                                "international_org", "news", "book", "tourism_board",
                                                                "primary", "encyclopedia", "other"]},
                    "source_date": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["claim", "section", "importance", "source", "source_url", "source_type", "source_date", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}


def run(p, episode_id: int) -> None:
    st = get_storage()
    with session() as s:
        ep = s.get(Episode, episode_id)
        dest, angle = ep.destination, ep.topic

    if not st.exists(episode_id, "research", "notes.md"):
        # 先讀免費的維基百科條目；付費的網路搜尋只補缺口（每集有 Claude 花費上限）
        brief = read_brief(episode_id) or {}
        docs = freesources.gather([(dest, "zh"), (f"{dest} {angle}"[:60], "zh")]
                                  + [(k, "en") for k in brief.get("search_keywords_en", [])[:3]])
        st.write_json(episode_id, "research", "free_sources.json", docs)
        system = f"{EDITORIAL_DNA}\n\n你是研究員。{SOURCE_POLICY}"
        prompt = (
            f"目的地：{dest}\n本集核心問題：{angle}\n\n{brief_for(episode_id, 'research')}\n\n"
            f"為一支約 {get_settings().target_video_length_minutes} 分鐘、只講一個有趣故事的短紀錄片收集研究資料。"
            "不要寫通史、不要面面俱到：只挑能回答本集核心問題、而且讓人覺得「真的假的？」的具體事實、人物、場景與細節。\n"
            "觀眾對這個地方幾乎一無所知，所以附上 2–3 句定位資料（在世界哪一區、屬於哪個國家、主要語言）；"
            "故事中出現的關鍵人物也各寫一句「他們是誰」。\n"
            "先使用下方的免費資料（維基百科）；它們不足以回答的地方，才用網路搜尋補充（搜尋次數有限，請用在最關鍵的缺口）。\n"
            "每一個具體事實（數字、日期、排名、人名）後面都附上來源名稱與網址（維基百科條目也可以當來源）。整理成條列式研究筆記（繁體中文）。\n\n"
            f"## 免費資料（維基百科）\n{freesources.as_text(docs) or '（無）'}"
        )
        r = p.research.research("research", system, prompt, episode_id)
        st.write_text(episode_id, "research", "notes.md", r.text)
        st.write_json(episode_id, "research", "search_sources.json",
                      r.sources + [{"url": d["url"], "title": f"Wikipedia - {d['title']}", "page_age": ""} for d in docs])

    if st.exists(episode_id, "research", "claims.json"):
        return
    notes = st.path(episode_id, "research", "notes.md").read_text(encoding="utf-8")
    sources = st.read_json(episode_id, "research", "search_sources.json")
    prompt = (
        f"以下是 {dest} 的研究筆記與搜尋到的來源清單。把筆記中所有可查核的事實拆成獨立 claim（一句話一個事實）。\n"
        "每個 claim 必須對應筆記中標示的來源；若筆記沒有給來源，source_url 留空字串、confidence 給 0.3 以下。"
        "confidence 為 0~1，代表來源品質與一致性。importance：影響故事主線的數字/日期/因果為 key。\n\n"
        f"## 研究筆記\n{notes}\n\n## 搜尋來源\n" + "\n".join(f"- {x['title']} {x['url']}" for x in sources)
    )
    data = p.llm.json("research_extract", EDITORIAL_DNA, prompt, CLAIMS_SCHEMA, episode_id, effort="medium")
    claims = data["claims"]
    st.write_json(episode_id, "research", "claims.json", claims)
    with session() as s:
        s.execute(delete(ResearchSource).where(ResearchSource.episode_id == episode_id))
        for c in claims:
            s.add(ResearchSource(episode_id=episode_id, claim=c["claim"], source=c["source"], source_url=c["source_url"],
                                 source_type=c["source_type"], source_date=c["source_date"], confidence=c["confidence"]))
