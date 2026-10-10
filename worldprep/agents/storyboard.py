import re

from ..storage import get_storage
from .prompts import EDITORIAL_DNA
from .topic import brief_for

VISUAL_TYPES = ["slide", "chart", "title_card"]
SHOT_TYPES = ["people", "object", "map", "then_now", "daily_life", "landmark"]
MOTIONS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "static"]
MAX_SCENE_CHARS = 45

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_id": {"type": "string"},
                    "visual_type": {"type": "string", "enum": VISUAL_TYPES},
                    "visual_description": {"type": "string"},
                    "shot_type": {"type": "string", "enum": SHOT_TYPES},
                    "slide_headline": {"type": "string"},
                    "slide_number": {"type": "string"},
                    "search_query": {"type": "string"},
                    "ai_prompt": {"type": "string"},
                    "realistic": {"type": "boolean"},
                    "camera_motion": {"type": "string", "enum": MOTIONS},
                    "transition": {"type": "string", "enum": ["fade", "cut", "dissolve"]},
                    "on_screen_text": {"type": "string"},
                    "map_required": {"type": "boolean"},
                    "chart_required": {"type": "boolean"},
                    "map_place": {"type": "string"},
                    "map_caption": {"type": "string"},
                    "chart": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "unit": {"type": "string"},
                            "kind": {"type": "string", "enum": ["bar", "line"]},
                            "labels": {"type": "array", "items": {"type": "string"}},
                            "values": {"type": "array", "items": {"type": "number"}},
                            "source": {"type": "string"},
                            "claim_id": {"type": "integer"},
                        },
                        "required": ["title", "unit", "kind", "labels", "values", "source", "claim_id"],
                        "additionalProperties": False,
                    },
                },
                "required": ["scene_id", "visual_type", "visual_description", "shot_type", "slide_headline", "slide_number", "search_query", "ai_prompt", "realistic",
                             "camera_motion", "transition", "on_screen_text", "map_required", "chart_required",
                             "map_place", "map_caption", "chart"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scenes"],
    "additionalProperties": False,
}


def split_scenes(text: str, max_chars: int = MAX_SCENE_CHARS) -> list[str]:
    sentences = [x for x in re.split(r"(?<=[。！？!?])", text) if x.strip()]
    chunks, cur = [], ""
    for sen in sentences:
        if cur and len(cur) + len(sen) > max_chars:
            chunks.append(cur)
            cur = sen
        else:
            cur += sen
    if cur:
        chunks.append(cur)
    return chunks


def base_scenes(script: dict) -> list[dict]:
    scenes = []
    for si, sec in enumerate(script["sections"]):
        for pi, para in enumerate(sec["paragraphs"]):
            for ci, chunk in enumerate(split_scenes(para["text"])):
                scenes.append({
                    "scene_id": f"s{len(scenes) + 1:03d}",
                    "section": sec["section"],
                    "heading": sec["heading"],
                    "section_start": pi == 0 and ci == 0,
                    "script_text": chunk.strip(),
                    "claim_ids": para["claim_ids"],
                })
    return scenes


def run(p, episode_id: int) -> None:
    st = get_storage()
    if st.exists(episode_id, "scripts", "storyboard.json"):
        return
    script = st.read_json(episode_id, "scripts", "script.json")
    scenes = base_scenes(script)
    from .script import _facts

    _, fact_text = _facts(episode_id)
    listing = "\n".join(f"{s['scene_id']} [{s['section']}] {s['script_text']}" for s in scenes)
    prompt = (
        "為下列每個場景設計一張投影片式畫面（每個 scene_id 恰好一筆，順序相同）。畫面由 AI 生圖產生，電影感、寫實或老照片質感。\n"
        "visual_type：預設 slide；需要比較多個數字（人口、GDP、產業占比、成長）時用 chart（全集最多 6 個；數字只能取自下方已查核事實，"
        "values 必須與事實原文完全相同、不可換算，萬/億等單位寫在 unit，並填 claim_id 與 source）；title_card 不要使用。\n"
        "slide 的欄位：\n"
        "- 畫面必須畫出這句旁白裡的具體名詞或動作：沒聽到旁白的人，看畫面也要大概猜得到在講什麼。不要用和旁白無關的漂亮風景帶過。\n"
        "- shot_type 依旁白內容選一種：\n"
        "  · people：講到某些人在做某件事（工人、商人、船員、居民的動作與場面；真實歷史人物不畫臉）\n"
        "  · object：講到具體的東西（貨物、工具、食物、交通工具、建築細節的特寫）\n"
        "  · map：講到位置、航線、移動、範圍（簡化地圖＋路線或範圍）\n"
        "  · then_now：講到「以前／現在」的對比（同一個地方，左半邊當年、右半邊今天）\n"
        "  · daily_life：講到今天的人怎麼生活（市場、通勤、街頭小吃、節慶）\n"
        "  · landmark：講到這個地方本身或景點（地標、建築、風景）\n"
        "  多樣性規則：landmark 全集不超過三成（主要留給 attractions 段落）；同一種 shot_type 不可連續超過 2 個場景；每個段落至少用到 3 種。\n"
        "- 例外：geography 段落的第一個場景固定是 map 定位地圖——觀眾對這個地方一無所知，畫一張簡化的區域地圖，標出這座城市的位置，"
        "slide_headline 填城市或國家名，最多再標一個鄰近的海或大區域名稱。\n"
        "- slide_headline：大多數投影片留空字串（純畫面、無字）。只在段落開頭、或需要點出地名時填寫，"
        "內容以地名或景點名為主（繁體中文 8 字內，例如「大稻埕」「淡水紅毛城」），全集有字的投影片不超過三成，且不可重複。\n"
        "- slide_number：只有當畫面需要強調一個關鍵數字時才填（含單位，例如「44.50%」，必須與已查核事實原文完全相同），全集最多 8 張；其餘填空字串。\n"
        "- visual_description：用英文具體描述畫面（哪個地點、時代、角度、天氣光線、構圖），時代與地點必須正確（台灣的場景不要畫成日本或中國大陸）。"
        "畫到真實的歷史人物時，只能用背影、剪影、遠景或代表物件，不可畫出可辨識的臉。"
        "需要地圖時畫成簡化的輪廓示意，最多兩個地名，且地名必須出現在旁白中。\n"
        "- search_query：3–6 個英文關鍵字，生圖失敗時用來搜尋備用圖庫。\n"
        "其他欄位：camera_motion 一律 static；on_screen_text、ai_prompt、map_place、map_caption 填空字串；realistic、map_required、chart_required 依實際填寫；"
        "chart 不需要時填 title=''、labels=[]、values=[]、claim_id=0。\n\n"
        f"## 已查核事實\n{fact_text}\n\n{brief_for(episode_id, 'visual')}\n\n## 場景\n{listing}"
    )
    data = p.llm.json("storyboard", f"{EDITORIAL_DNA}\n\n你是紀錄片分鏡導演與剪輯師。", prompt, SCENE_SCHEMA, episode_id, effort="medium")
    by_id = {x["scene_id"]: x for x in data["scenes"]}
    for sc in scenes:
        v = by_id.get(sc["scene_id"]) or {"visual_type": "slide", "visual_description": sc["script_text"],
                                         "shot_type": "landmark", "slide_headline": sc["heading"], "slide_number": "", "search_query": "",
                                         "ai_prompt": "", "realistic": True, "camera_motion": "static", "transition": "fade",
                                         "on_screen_text": "", "map_required": False, "chart_required": False,
                                         "map_place": "", "map_caption": "", "chart": {"title": "", "labels": [], "values": []}}
        v = {k: val for k, val in v.items() if k != "scene_id"}
        sc.update(v)
        if sc["visual_type"] == "slide":
            # 投影片不晃動；大標已畫在圖上，不再疊字卡
            sc["camera_motion"], sc["on_screen_text"] = "static", ""
        sc["source_type"] = sc["visual_type"]
        sc["source_reference"] = ""
        sc["duration"] = None
    st.write_json(episode_id, "scripts", "storyboard.json", scenes)
