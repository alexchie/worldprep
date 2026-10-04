import re

from ..storage import get_storage
from .prompts import EDITORIAL_DNA

VISUAL_TYPES = ["stock_video", "stock_photo", "archive_image", "ai_image", "map", "chart", "title_card"]
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
                "required": ["scene_id", "visual_type", "visual_description", "search_query", "ai_prompt", "realistic",
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
        "為下列每個場景設計畫面（每個 scene_id 恰好一筆，順序相同）。\n"
        "visual_type 選擇原則：今日城市街景、人潮、交通、天際線、食物、文化活動 → stock_video（實拍動態片段，優先使用，約佔一半場景）；"
        "需要特定靜態畫面的今日景物 → stock_photo；歷史事件、古地圖、老照片、歷史畫作、歷史人物 → archive_image（來自 Wikimedia Commons）；"
        "以上都找不到的歷史重建或概念場景 → ai_image（ai_prompt 用英文詳述，realistic 表示是否為寫實風格）。"
        "search_query 一律用精準英文關鍵字（3–6 個字）：stock 類例如 'Shibuya crossing night aerial'；"
        "archive_image 要包含地名＋年代或事件名，例如 'Edo period map Tokyo 1840s'、'Great Kanto earthquake 1923 ruins'；"
        "地理、貿易路線、位置 → map（map_place、map_caption）；人口、GDP、產業、成長數據 → chart（數字只能取自下方已查核事實，values 必須與事實原文中的數字完全相同、不可換算，萬/億等單位寫在 unit，並填 claim_id 與 source）；"
        "章節轉換或關鍵概念 → title_card。\n"
        "保持視覺多樣：同一種 visual_type 不可連續超過 3 個場景，同一畫面不重複。AI 畫面不超過總場景 25%。"
        "on_screen_text 是畫面上的短字卡（16 字內，僅在重要數字/地名/年份時使用，其餘留空字串）。"
        "不需要的欄位填空字串或 false；chart 不需要時填 title=''、labels=[]、values=[]、claim_id=0。\n\n"
        f"## 已查核事實\n{fact_text}\n\n## 場景\n{listing}"
    )
    data = p.llm.json("storyboard", f"{EDITORIAL_DNA}\n\n你是紀錄片分鏡導演與剪輯師。", prompt, SCENE_SCHEMA, episode_id, effort="medium")
    by_id = {x["scene_id"]: x for x in data["scenes"]}
    for sc in scenes:
        v = by_id.get(sc["scene_id"]) or {"visual_type": "title_card", "visual_description": sc["heading"], "search_query": "",
                                         "ai_prompt": "", "realistic": False, "camera_motion": "static", "transition": "fade",
                                         "on_screen_text": "", "map_required": False, "chart_required": False,
                                         "map_place": "", "map_caption": "", "chart": {"title": "", "labels": [], "values": []}}
        v = {k: val for k, val in v.items() if k != "scene_id"}
        sc.update(v)
        sc["source_type"] = sc["visual_type"]
        sc["source_reference"] = ""
        sc["duration"] = None
    st.write_json(episode_id, "scripts", "storyboard.json", scenes)
