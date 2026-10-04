from sqlalchemy import delete

from ..db import session
from ..models import Episode, ResearchSource
from ..storage import get_storage
from .prompts import EDITORIAL_DNA, SOURCE_POLICY

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
        system = f"{EDITORIAL_DNA}\n\n你是研究員。{SOURCE_POLICY}"
        prompt = (
            f"目的地：{dest}\n本集核心問題：{angle}\n\n"
            "請用網路搜尋，為一支 12 分鐘的紀錄片收集研究資料。只挑能解釋「今天的樣子」的關鍵事實，不要寫完整通史。\n"
            "涵蓋：地理位置為何重要（貿易、防禦、移民、氣候、港口、交通）；塑造今日的關鍵歷史事件；城市結構、人口、交通、建築、主要區域；"
            "主要產業、貿易、代表企業、金融、經濟政策與全球連結（這座城市怎麼賺錢、為什麼產業在這裡發展）；"
            "歷史+城市+商業如何塑造食物、生活方式、語言、娛樂、宗教、社會規範；最後是能被前述故事解釋的代表景點。\n"
            "每一個具體事實（數字、日期、排名、公司資訊）後面都附上來源名稱、網址與資料日期。"
            "人口、GDP 等統計請用最新官方數字並註明年份。整理成條列式研究筆記（繁體中文）。"
        )
        r = p.research.research("research", system, prompt, episode_id)
        st.write_text(episode_id, "research", "notes.md", r.text)
        st.write_json(episode_id, "research", "search_sources.json", r.sources)

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
