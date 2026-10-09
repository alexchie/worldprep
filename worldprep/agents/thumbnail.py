import shutil
from pathlib import Path

from ..brand import CHANNEL_NAME
from ..db import session
from ..models import Episode
from ..render import cards
from ..storage import get_storage
from .prompts import EDITORIAL_DNA

CRITERIA = ["click_appeal", "visual_clarity", "destination_recognition", "curiosity", "mobile_readability",
            "brand_consistency", "factual_accuracy"]

SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "phrase": {"type": "string"},
                    "sub_phrase": {"type": "string"},
                    "scene_id": {"type": "string"},
                    "rationale": {"type": "string"},
                    "scores": {"type": "object", "properties": {c: {"type": "number"} for c in CRITERIA},
                               "required": CRITERIA, "additionalProperties": False},
                },
                "required": ["phrase", "sub_phrase", "scene_id", "rationale", "scores"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["concepts"],
    "additionalProperties": False,
}


def rule_errors(c: dict, main_title: str) -> list[str]:
    """縮圖文案的硬規則：主大字 4–10 字、不可重複標題、金色小標不可重複右上角的頻道/EP 徽章。"""
    errors = []
    phrase, sub = c["phrase"].strip(), c["sub_phrase"].strip()
    if not 4 <= len(phrase) <= 10:
        errors.append(f"主大字 {len(phrase)} 字")
    if phrase in main_title or main_title.startswith(phrase[:6]):
        errors.append("主大字重複標題")
    if "EP" in sub.upper() or CHANNEL_NAME in sub or CHANNEL_NAME in phrase:
        errors.append("文案重複頻道徽章")
    return errors


def run(p, episode_id: int, feedback: str = "", force: bool = False) -> Path:
    st = get_storage()
    final = st.path(episode_id, "thumbnails", "thumbnail.jpg")
    if final.exists() and not force:
        return final
    meta = st.read_json(episode_id, "final", "metadata.json")
    manifest = st.read_json(episode_id, "assets", "manifest.json")
    scenes = st.read_json(episode_id, "scripts", "storyboard.json")
    with session() as s:
        ep = s.get(Episode, episode_id)
        n, dest = ep.episode_number, ep.destination
    photo_scenes = [sc for sc in scenes if manifest[sc["scene_id"]]["asset_type"] in ("stock_photo", "stock_video", "archive_image", "ai_image")
                    and not manifest[sc["scene_id"]]["source"].startswith("original")]
    listing = "\n".join(f"{sc['scene_id']}: {sc['visual_description']}" for sc in photo_scenes) or "（無照片，使用品牌底圖，scene_id 填空字串）"
    data = p.llm.json(
        "thumbnail", f"{EDITORIAL_DNA}\n\n你是 YouTube 縮圖設計師。品牌視覺：電影感、高對比、深海軍藍、暖金點綴、白色字、單一主體、極簡。",
        f"影片標題：{meta['title']}\n目的地：{dest}\n\n請提出 3 個縮圖概念。phrase 為主大字（4–10 字，與標題互補而非重複，要讓人想問「為什麼？」），"
        "sub_phrase 為金色小標（2–8 字，可為目的地名或年份），scene_id 從下列畫面選一張最有辨識度的主視覺。不要寫成段落。每個面向 0–10 分。"
        + (f"\n製作人回饋：{feedback}" if feedback else "") + f"\n\n## 可用畫面\n{listing}",
        SCHEMA, episode_id, effort="low",
    )
    best, best_score = None, -1.0
    for i, c in enumerate(data["concepts"][:5]):
        m = manifest.get(c["scene_id"], {})
        bg = m.get("poster") or m.get("file_path")
        out = st.path(episode_id, "thumbnails", f"candidate_{i + 1}.jpg")
        cards.thumbnail(Path(bg) if bg else None, c["phrase"], c["sub_phrase"], n, out)
        c["file"] = str(out)
        c["total"] = sum(c["scores"].values()) / len(CRITERIA)
        c["rule_errors"] = rule_errors(c, meta["main_title"])
        if c["rule_errors"]:
            c["total"] -= 10
        if c["total"] > best_score:
            best, best_score = c, c["total"]
    st.write_json(episode_id, "thumbnails", "concepts.json", data["concepts"])
    shutil.copy(best["file"], final)
    return final
