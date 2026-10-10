from pathlib import Path

from PIL import Image

from .. import edition
from ..brand import ep_label
from ..config import ROOT, get_settings
from ..db import session
from ..logging_setup import log
from ..models import Episode
from ..render import cards
from ..storage import get_storage
from .topic import read_brief

COVER_DIR = ROOT / "cover_sample"

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "country": {"type": "string"},
        "city": {"type": "string"},
        "title_lines": {"type": "array", "items": {"type": "string"}},
        "gold_keywords": {"type": "array", "items": {"type": "string"}},
        "subtitle": {"type": "string"},
        "core_question": {"type": "string"},
        "direction": {"type": "string", "enum": ["歷史", "城市", "商業", "文化", "綜合"]},
        "landmarks": {"type": "array", "items": {"type": "string"}},
        "mood": {"type": "string"},
        "scene": {"type": "string"},
    },
    "required": ["country", "city", "title_lines", "gold_keywords", "subtitle", "core_question", "direction", "landmarks",
                 "mood", "scene"],
    "additionalProperties": False,
}

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "single_thumbnail": {"type": "boolean"},
        "title_exact": {"type": "boolean"},
        "brand_exact": {"type": "boolean"},
        "episode_exact": {"type": "boolean"},
        "tags_exact": {"type": "boolean"},
        "no_extra_or_garbled_text": {"type": "boolean"},
        "landmarks_correct": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": ["single_thumbnail", "title_exact", "brand_exact", "episode_exact", "tags_exact", "no_extra_or_garbled_text",
                 "landmarks_correct", "note"],
    "additionalProperties": False,
}


TEXT_KEYS = ("single_thumbnail", "title_exact", "brand_exact", "episode_exact", "tags_exact", "no_extra_or_garbled_text")


def cover_memory() -> str:
    """頻道主的封面規範（cover_sample/*.md），每次製作都重新讀取。"""
    return "\n\n".join(f.read_text(encoding="utf-8").strip() for f in sorted(COVER_DIR.glob("*.md")))


def cover_references() -> list[Path]:
    return sorted(f for f in COVER_DIR.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg")) if COVER_DIR.exists() else []


def plan_cover(p, episode_id: int, title: str, dest: str, n: int, feedback: str = "") -> dict:
    """依標題與企劃，填好封面規範第十一節的「本次任務輸入資料」。"""
    b = read_brief(episode_id) or {}
    ed = edition.current()
    lang_rule = ("" if ed.lang == "zh" else
                 "\n\n這是英文頻道的封面：title_lines、gold_keywords、subtitle 一律寫英文（每行 2–5 個完整英文單字，不可把一個單字拆到兩行，subtitle 6 個單字內），"
                 "其餘欄位照常。")
    return p.llm.json(
        "thumbnail", f"你是《{ed.channel}》的縮圖藝術總監。以下是頻道主的封面規範，請嚴格遵守。\n\n{cover_memory()}",
        f"請為本集填寫封面的「本次任務輸入資料」。\n\n集數：{ep_label(n)}\nYouTube 完整影片標題：{title}\n目的地：{dest}\n"
        f"核心問題：{b.get('core_question', '')}\n畫面方向：{b.get('visual_direction', '')}\n\n"
        "欄位說明：\n"
        "- title_lines：縮圖主標題，分成 2–3 行（每行 4–11 字）。從上架標題取出最有吸引力的問題與關鍵詞，"
        "可以精簡，但不可加入標題沒有的事實；要像範本那樣是一個讓人想知道答案的問題。\n"
        "- gold_keywords：title_lines 中要用金色強調的 1–2 個詞，必須逐字出現在 title_lines 裡。\n"
        "- subtitle：一行內的副標（12 字內）；不需要時填空字串。\n"
        "- direction：本集主要方向。landmarks：1–2 個真的位於該地、而且能自然出現在同一個畫面的地標或視覺元素"
        "（不要把相隔很遠的地點硬拼在一起）。\n"
        "- mood：希望呈現的情緒。scene：用英文描述一個具體的主視覺構圖（哪個地標、時間、光線、角度），"
        "要一眼看出是哪個城市，且呼應本集故事，不要只是把範本的地標換掉。"
        + lang_rule + (f"\n\n頻道主回饋：{feedback}" if feedback else ""),
        PLAN_SCHEMA, episode_id,
    )


def cover_prompt(plan: dict, n: int) -> str:
    ed = edition.current()
    brand = "」「".join(ed.brand_lines)
    lang_rule = ("" if ed.lang == "zh" else
                 "這是英文頻道版本：畫面上所有文字都是英文，不可出現任何中文字；參考圖裡的中文品牌與標籤一律換成上述英文。")
    title = "\n".join(plan["title_lines"])
    gold = "、".join(plan["gold_keywords"]) or "（無）"
    return (
        f"{cover_memory()}\n\n"
        "## 本次任務輸入資料\n"
        f"【國家】：{plan['country']}\n【城市或地區】：{plan['city']}\n【集數】：{ep_label(n)}\n"
        f"【縮圖主標題】（逐字照抄，分行如下）：\n{title}\n【金色關鍵詞】：{gold}\n"
        f"【副標題】：{plan['subtitle'] or '無'}\n【影片核心問題】：{plan['core_question']}\n【本集主要方向】：{plan['direction']}\n"
        f"【必須出現的地標或視覺元素】：{'、'.join(plan['landmarks'])}\n【希望呈現的情緒】：{plan['mood']}\n"
        f"【主視覺構圖】：{plan['scene']}\n"
        f"【其他限制】：左上角品牌固定為「{brand}」（參考圖裡的舊英文品牌 WORLD WISE 已停用）；底部五個標籤固定為「{' | '.join(ed.tags)}」；"
        "除上述文字與主標題、副標題外不得有任何其他文字。附上的參考圖是系列設計規範，只輸出一張全新的單集縮圖。"
        + lang_rule
    )


def check_cover(p, image: Path, plan: dict, n: int, episode_id: int) -> dict:
    if not hasattr(p.llm, "vision_json"):
        return {"ok": True, "note": ""}
    ed = edition.current()
    # 英文標題各行之間有空格，不能直接黏起來比對
    title = " ".join(plan["title_lines"]) if ed.lang == "en" else "".join(plan["title_lines"])
    english = ("\n英文版額外要求（任一不符就判不合格）：每個英文單字完整、沒有被拆到兩行；單字之間的空格都在；"
               f"右上角的集數徽章只寫英文「{ep_label(n)}」，整張圖（包含右上角與左上角）不可出現任何中文字。") if ed.lang == "en" else ""
    prompt = (f"指定主標題（逐字）：{title}\n指定分行：{' / '.join(plan['title_lines'])}\n副標題：{plan['subtitle'] or '無'}\n"
              f"品牌：{'／'.join(ed.brand_lines)}\n集數：{ep_label(n)}\n底部標籤：{'、'.join(ed.tags)}\n"
              f"應出現的地標：{'、'.join(plan['landmarks'])}（位於 {plan['city']}）\n\n"
              "single_thumbnail：是否只有一張完整縮圖（不是九宮格或拼貼）。title_exact：主標題是否逐字正確（換行位置不影響）。"
              "brand_exact／episode_exact／tags_exact：各自是否逐字正確。no_extra_or_garbled_text：是否沒有其他多餘文字、亂碼或簡體字。"
              "landmarks_correct：畫面中出現的地標是否沒有畫錯、沒有不合理的拼接（不要求列出的地標全部出現）。"
              f"note 寫出發現的問題。{english}")
    v = p.llm.vision_json("cover_check", "你是封面校對員，逐字核對圖片上實際出現的文字，不要猜測。", prompt, [image], CHECK_SCHEMA, episode_id)
    v["ok"] = all(v[k] for k in CHECK_SCHEMA["required"] if k != "note")
    if ed.lang == "en" and all(v[k] for k in TEXT_KEYS):
        # 英文封面的文字再由 Opus 獨立校對一次，兩道都過才算文字正確
        strict = p.llm.vision_json("cover_check_strict", "你是嚴格的英文封面校對員，逐字逐行核對，有任何疑慮就判不合格。",
                                   prompt, [image], CHECK_SCHEMA, episode_id)
        if not all(strict[k] for k in TEXT_KEYS):
            return {**strict, "ok": False, "note": f"Opus 複核：{strict['note']}"}
    return v


def _finalize(src: Path, final: Path) -> Path:
    """YouTube 縮圖：1280×720、JPEG、小於 2MB。"""
    Image.open(src).convert("RGB").resize((1280, 720), Image.LANCZOS).save(final, quality=90, optimize=True)
    return final


def _fallback(p, episode_id: int, plan: dict, n: int, final: Path) -> Path:
    """生圖寫不出正確的字時：照規範第九節，改生成無字底圖，再由程式排版文字。"""
    st = get_storage()
    bg = None
    if p.slides:
        prompt = (f"A 16:9 cinematic YouTube thumbnail background, no text, no letters, no logos. {plan['scene']}. "
                  "Deep navy and warm gold grading, dramatic light, empty space on the left half for a large title.")
        try:
            bg = p.slides.get(prompt, st.path(episode_id, "thumbnails", "background"), episode_id).path
        except Exception as e:
            log.warning("cover_background_failed", extra={"episode_id": episode_id, "err": str(e)[:300]})
    out = st.path(episode_id, "thumbnails", "fallback.jpg")
    lines = plan["title_lines"] if edition.current().lang == "en" else "".join(plan["title_lines"])  # 英文不可拆單字
    cards.thumbnail(bg, lines, plan["subtitle"] or plan["city"], n, out)
    return _finalize(out, final)


def run(p, episode_id: int, feedback: str = "", force: bool = False, title: str | None = None) -> Path:
    st = get_storage()
    final = st.path(episode_id, "thumbnails", "thumbnail.jpg")
    if final.exists() and not force:
        return final
    with session() as s:
        ep = s.get(Episode, episode_id)
        n, dest, title = ep.episode_number, ep.destination, title or ep.title
    plan = plan_cover(p, episode_id, title, dest, n, feedback)
    attempts = []
    if p.slides and hasattr(p.slides, "compose"):
        prompt, refs = cover_prompt(plan, n), cover_references()
        attempts_max = get_settings().cover_attempts + (2 if edition.current().lang == "en" else 0)  # 英文多給兩次機會
        for i in range(attempts_max):
            try:
                raw = p.slides.compose(prompt, refs, st.path(episode_id, "thumbnails", f"raw_{i + 1}"), episode_id).path
                img = _finalize(raw, st.path(episode_id, "thumbnails", f"candidate_{i + 1}.jpg"))
            except Exception as e:
                log.warning("cover_generation_failed", extra={"episode_id": episode_id, "err": str(e)[:300]})
                break
            check = check_cover(p, img, plan, n, episode_id)
            attempts.append({"file": str(img), **check})
            if check["ok"]:
                break
    st.write_json(episode_id, "thumbnails", "cover.json", {"plan": plan, "attempts": attempts})
    # 全部通過優先；否則退而求其次：文字全對（英文版含 Opus 複核）、只是地標不完美的那張，也比程式排字的備案好
    good = (next((a for a in attempts if a["ok"]), None)
            or next((a for a in attempts if all(a.get(k) for k in TEXT_KEYS)), None))
    if good:
        return _finalize(Path(good["file"]), final)
    log.warning("cover_fallback", extra={"episode_id": episode_id, "notes": [a["note"][:200] for a in attempts]})
    return _fallback(p, episode_id, plan, n, final)
