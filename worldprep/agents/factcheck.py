from sqlalchemy import select

from ..db import audit, session
from ..models import Episode, ResearchSource
from ..storage import get_storage
from ..providers import freesources
from .prompts import EDITORIAL_DNA, SOURCE_POLICY

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["verified", "qualified", "unverified", "false"]},
                    "final_claim": {"type": "string"},
                    "source": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_date": {"type": "string"},
                    "confidence": {"type": "number"},
                    "note": {"type": "string"},
                },
                "required": ["id", "verdict", "final_claim", "source", "source_url", "source_date", "confidence", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

MIN_CONFIDENCE = 0.6
HUMAN_REVIEW_RATIO = 0.3


def run(p, episode_id: int) -> None:
    st = get_storage()
    with session() as s:
        ep = s.get(Episode, episode_id)
        dest = ep.destination
        rows = list(s.scalars(select(ResearchSource).where(ResearchSource.episode_id == episode_id).order_by(ResearchSource.id)))
        claims = [{"id": r.id, "claim": r.claim, "source": r.source, "source_url": r.source_url, "source_date": r.source_date}
                  for r in rows]

    if not st.exists(episode_id, "research", "factcheck_notes.md"):
        system = f"{EDITORIAL_DNA}\n\n你是嚴格的事實查核員，與原研究員無關。{SOURCE_POLICY}"
        listing = "\n".join(f"[{c['id']}] {c['claim']}（原來源：{c['source']} {c['source_url']} {c['source_date']}）" for c in claims)
        free = st.read_json(episode_id, "research", "free_sources.json") if st.exists(episode_id, "research", "free_sources.json") else []
        prompt = (
            f"目的地：{dest}。請獨立查核下列每一條 claim，特別注意日期、人名、數字、排名與因果主張。\n"
            "先用下方的免費資料（維基百科）核對；免費資料無法確認的關鍵 claim，才用網路搜尋（搜尋次數有限，留給最重要的數字與年份）。\n"
            "每條都要寫出：[id] 判定（正確 / 需修正或加限定 / 無法證實 / 錯誤）、依據的來源名稱、網址，以及修正後的正確敘述。\n\n"
            + listing + f"\n\n## 免費資料（維基百科）\n{freesources.as_text(free) or '（無）'}"
        )
        r = p.research.research("factcheck", system, prompt, episode_id)
        st.write_text(episode_id, "research", "factcheck_notes.md", r.text)
        st.write_json(episode_id, "research", "factcheck_sources.json", r.sources)

    notes = st.path(episode_id, "research", "factcheck_notes.md").read_text(encoding="utf-8")
    prompt = (
        "把以下查核筆記轉成結構化結果，每個 claim id 一筆。verdict：verified=查證正確；qualified=修正或加限定後可用（final_claim 寫修正版）；"
        "unverified=找不到可靠來源；false=錯誤。final_claim 一律寫成可直接用在腳本中的正確敘述。confidence 0~1。\n\n"
        f"## 原始 claims\n" + "\n".join(f"[{c['id']}] {c['claim']}" for c in claims) + f"\n\n## 查核筆記\n{notes}"
    )
    results = p.llm.json("factcheck_extract", EDITORIAL_DNA, prompt, VERDICT_SCHEMA, episode_id, effort="medium")["results"]
    by_id = {r["id"]: r for r in results}
    flagged = 0
    with session() as s:
        rows = list(s.scalars(select(ResearchSource).where(ResearchSource.episode_id == episode_id)))
        for row in rows:
            r = by_id.get(row.id)
            if r is None:
                row.verdict = "unverified"
                flagged += 1
                continue
            verdict = r["verdict"]
            if verdict in ("verified", "qualified") and r["confidence"] < MIN_CONFIDENCE:
                verdict = "unverified"
            row.verdict = verdict
            row.confidence = r["confidence"]
            if verdict in ("verified", "qualified"):
                row.claim = r["final_claim"] or row.claim
                if r["source_url"]:
                    row.source, row.source_url, row.source_date = r["source"], r["source_url"], r["source_date"]
            else:
                flagged += 1
        ep = s.get(Episode, episode_id)
        if rows and flagged / len(rows) > HUMAN_REVIEW_RATIO:
            ep.needs_human_review = True
        audit(s, "factcheck_done", episode_id, total=len(rows), flagged=flagged)
    st.write_json(episode_id, "research", "factcheck.json", results)
