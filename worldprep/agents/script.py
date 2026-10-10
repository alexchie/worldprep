from sqlalchemy import select

from ..brand import BANNED_OPENINGS
from ..config import ROOT, get_settings
from ..db import session
from ..models import Episode, ResearchSource
from ..storage import get_storage
from .prompts import EDITORIAL_DNA
from .topic import brief_for, read_brief, set_title

OPENING_RULES = ROOT / "opening" / "opening_prompt.txt"

SECTION_ORDER = ["hook", "geography", "history", "city", "business", "culture", "attractions", "closing"]

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "thesis": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": SECTION_ORDER},
                    "heading": {"type": "string"},
                    "causal_link": {"type": "string"},
                    "paragraphs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "claim_ids": {"type": "array", "items": {"type": "integer"}},
                            },
                            "required": ["text", "claim_ids"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["section", "heading", "causal_link", "paragraphs"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["thesis", "sections"],
    "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "coherent": {"type": "boolean"},
        "follows_structure": {"type": "boolean"},
        "hook_strong": {"type": "boolean"},
        "natural_taiwanese_chinese": {"type": "boolean"},
        "sounds_ai_generated": {"type": "boolean"},
        "unsupported_sentences": {"type": "array", "items": {"type": "string"}},
        "issues": {"type": "array", "items": {"type": "string"}},
        "title_supported": {"type": "boolean"},
        "title_fix": {"type": "string"},
        "score": {"type": "number"},
    },
    "required": ["pass", "coherent", "follows_structure", "hook_strong", "natural_taiwanese_chinese",
                 "sounds_ai_generated", "unsupported_sentences", "issues", "title_supported", "title_fix", "score"],
    "additionalProperties": False,
}

SCRIPT_RULES = """寫作規則：
- 這是旁白稿，會被唸出來。口語、電影感、聰明、精簡、故事驅動。句子長短交錯，避免重複句型與明顯 AI 慣用語（例如「讓我們一起」「不僅…更是…」「在這個…的時代」「總而言之」）。
- 敘事結構：問題 → 背景 → 歷史 → 轉變 → 商業 → 文化 → 今天看到的樣子。
- sections 依序為 hook, geography, history, city, business, culture, attractions, closing。
- hook：開場 15–23 秒講完（字數範圍見下方），依照「開場規範」：第一句直接承接本集 YouTube 標題的問題（延伸而非逐字朗讀），接著用一個真實、反直覺的事實或矛盾讓觀眾想追下去，不在 hook 裡解答。禁止問候、頻道介紹、目錄式開場。
- hook 之後影片會自動插入固定品牌台詞，腳本裡不要寫品牌台詞；geography 段落要直接接續 hook 的謎題，不可再說「大家好」「今天我們要介紹」「本集從五個面向」之類的話。
- 每一段都要回答「觀眾為什麼要在乎」，並用 causal_link 說明它如何承接上一段、推動下一段。
- culture 不是獨立段落，要連回歷史、城市、商業。
- attractions 必須是整個故事的結果：不要說「這裡很漂亮」，要說「理解了 X，你再看這個地方，就會發現它其實是……」。
- closing 用一兩句收束核心問題，自然帶出「先看懂世界，再出發。」，不要喊口號式結尾、不要求訂閱。
- 只能使用提供的已查核事實中的數字、日期、排名與公司資訊；每段在 claim_ids 標註用到的事實編號。沒有查核過的具體數字一律不要寫。
- 段落長度約 60–140 字，方便配畫面。"""


def _facts(episode_id: int) -> tuple[list[dict], str]:
    with session() as s:
        rows = list(s.scalars(select(ResearchSource).where(ResearchSource.episode_id == episode_id,
                                                             ResearchSource.verdict.in_(["verified", "qualified"]))))
    facts = [{"id": r.id, "claim": r.claim, "source": r.source, "date": r.source_date} for r in rows]
    return facts, "\n".join(f"[{f['id']}] {f['claim']}（{f['source']} {f['date']}）" for f in facts)


def script_text(script: dict) -> str:
    return "\n\n".join(p["text"] for sec in script["sections"] for p in sec["paragraphs"])


def hook_char_limit() -> int:
    """依實測語速換算 hook 可用字數（扣掉句間停頓）。"""
    cfg = get_settings()
    return int((cfg.hook_max_seconds - 1.0) * cfg.effective_chars_per_minute / 60)


def hook_char_min() -> int:
    cfg = get_settings()
    return int((cfg.hook_min_seconds - 1.0) * cfg.effective_chars_per_minute / 60)


def structural_issues(script: dict, target_chars: int, valid_ids: set[int]) -> list[str]:
    issues = []
    hook = sum(len(par["text"]) for sec in script["sections"] if sec["section"] == "hook" for par in sec["paragraphs"])
    if not hook_char_min() <= hook <= hook_char_limit():
        issues.append(f"hook {hook} 字，應在 {hook_char_min()}–{hook_char_limit()} 字之間（15–23 秒）")
    order = [s["section"] for s in script["sections"]]
    filtered = [s for s in SECTION_ORDER if s in order]
    if order != filtered or any(x not in order for x in ["hook", "history", "city", "business", "culture", "attractions"]):
        issues.append(f"段落順序或缺漏不符合 歷史→城市→商業→文化→景點：{order}")
    text = script_text(script)
    first = text[:40]
    for b in BANNED_OPENINGS:
        if b in first:
            issues.append(f"開場使用了禁用句：{b}")
    n = len(text.replace("\n", ""))
    if abs(n - target_chars) / target_chars > 0.15:
        issues.append(f"總字數 {n}，目標約 {target_chars}（±15%）")
    bad = {cid for sec in script["sections"] for p in sec["paragraphs"] for cid in p["claim_ids"]} - valid_ids
    if bad:
        issues.append(f"引用了未查核的事實編號：{sorted(bad)}")
    return issues


def run(p, episode_id: int, feedback: str = "") -> None:
    st = get_storage()
    if st.exists(episode_id, "scripts", "script.json") and not feedback:
        return
    target = get_settings().target_chars
    with session() as s:
        ep = s.get(Episode, episode_id)
        dest, angle = ep.destination, ep.topic
    facts, fact_text = _facts(episode_id)
    valid = {f["id"] for f in facts}
    notes = st.path(episode_id, "research", "notes.md").read_text(encoding="utf-8")
    opening = OPENING_RULES.read_text(encoding="utf-8") if OPENING_RULES.exists() else ""
    system = (f"{EDITORIAL_DNA}\n\n你是頻道首席紀錄片編劇。\n{SCRIPT_RULES}\n- hook 全段 {hook_char_min()}–{hook_char_limit()} 字。"
              f"\n\n## 開場規範（頻道主提供；畫面、品牌動畫與剪輯由系統處理，你只負責 hook 旁白）\n{opening}")
    base = (
        f"目的地：{dest}\n本集核心問題：{angle}\n{brief_for(episode_id, 'script')}\n\n旁白總字數目標：約 {target} 字（依實測語速換算的 {get_settings().target_video_length_minutes} 分鐘）。\n\n"
        f"## 已查核事實（只能用這些具體數據）\n{fact_text}\n\n## 研究背景（脈絡參考，其中未查核的數字不可使用）\n{notes[:20000]}"
    )
    if feedback:
        base += f"\n\n## 製作人回饋（本次重寫必須處理）\n{feedback}"

    script = p.llm.json("script", system, base, SCRIPT_SCHEMA, episode_id, effort="high")
    review = None
    for round_ in range(2):
        issues = structural_issues(script, target, valid)
        review = p.llm.json(
            "script_review",
            f"{EDITORIAL_DNA}\n\n你是嚴格的總編輯，負責腳本審查。",
            "審查以下旁白稿：故事是否連貫？是否遵循 歷史→城市→商業→文化→景點 的因果鏈？hook 是否夠強？"
            "是否有未被已查核事實支持的主張（列出原句）？是否是自然的台灣繁體中文、不學術、不像 AI 寫的？有無不必要的重複？"
            "腳本是否走製作人指定的敘事弧線、開頭 hook 是否做到要求？"
            "title_supported：標題的每個承諾（數字、情緒詞、因果）是否都被已查核事實與腳本兌現；若否，title_fix 寫一個符合同一原型、"
            "只承諾已兌現內容的修正版主標題（30 字以內，不含「｜世界先修課 EP.xx」），若是則留空字串。"
            "score 0-10，>=7.5 且無未支持主張才 pass。\n\n"
            f"{brief_for(episode_id, 'review')}\n\n## 腳本\n{script_text(script)}",
            REVIEW_SCHEMA, episode_id, effort="medium",
            cached_prefix=f"## 已查核事實（審查依據）\n{fact_text}",
        )
        issues += review["issues"] + [f"未支持的主張：{x}" for x in review["unsupported_sentences"]]
        if review["pass"] and not structural_issues(script, target, valid):
            break
        if round_ == 1:
            break
        script = p.llm.json("script_revise", system,
                            base + "\n\n## 上一版腳本\n" + script_text(script) + "\n\n## 必須修正的問題\n- " + "\n- ".join(issues),
                            SCRIPT_SCHEMA, episode_id, effort="high")

    brief = read_brief(episode_id)
    if brief and review and not review["title_supported"] and review["title_fix"].strip():
        set_title(episode_id, review["title_fix"].strip(), brief["archetype"], "腳本審查：原標題承諾未被查核事實支持")
    st.write_json(episode_id, "scripts", "script.json", script)
    st.write_json(episode_id, "scripts", "review.json", review)
    st.write_text(episode_id, "scripts", "script.txt", script_text(script))
    with session() as s:
        ep = s.get(Episode, episode_id)
        ep.script = script_text(script)
        if review and not review["pass"]:
            ep.needs_human_review = True
