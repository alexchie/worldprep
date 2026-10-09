from pathlib import Path

from sqlalchemy import delete

from .. import costs
from ..db import session
from ..logging_setup import log
from ..models import Asset, Episode
from ..providers.base import ImageResult
from ..render import cards
from ..render.ffmpeg import poster_frame
from ..storage import get_storage


def _card(sc: dict, out: Path) -> ImageResult:
    cards.title_card(sc.get("on_screen_text") or sc["heading"], "", out.with_suffix(".jpg"))
    return ImageResult(out.with_suffix(".jpg"), source="original:title_card", creator="世界先修課",
                       license="Original", usage_rights="owned")


def _stock(p, sc: dict, out: Path, episode_id: int) -> ImageResult | None:
    if not p.stock_images or not sc.get("search_query"):
        return None
    return p.stock_images.get(sc["search_query"], out, episode_id)


def _video(p, sc: dict, out: Path, episode_id: int) -> ImageResult | None:
    if not p.stock_videos or not sc.get("search_query"):
        return None
    return p.stock_videos.get(sc["search_query"], out, episode_id)


def _archive(p, sc: dict, out: Path, episode_id: int) -> ImageResult | None:
    if not p.archive_images or not sc.get("search_query"):
        return None
    return p.archive_images.get(sc["search_query"], out, episode_id)


def _ai(p, sc: dict, out: Path, episode_id: int) -> ImageResult | None:
    if not p.ai_images or not sc.get("ai_prompt"):
        return None
    if not costs.check_budget(episode_id, costs.IMAGE_PER_IMAGE["openai"]):
        return None
    prompt = sc["ai_prompt"] + ". Cinematic documentary still, 16:9, no text, no watermark."
    return p.ai_images.get(prompt, out, episode_id, realistic=bool(sc.get("realistic")))


SLIDE_STYLE = (
    "Create one 16:9 slide for a cinematic documentary on a Taiwanese YouTube channel about why places became what they are. "
    "Visual style: full-bleed, photorealistic or archival-photo look of the exact period and place described, "
    "deep navy (#0b1b3a) shadows with warm gold (#d4a853) accents, editorial and elegant, consistent across the whole episode. "
    "Text: show ONLY the headline given below (Traditional Chinese as used in Taiwan, exactly as written, large, top-left area) "
    "and, if a key number is given, that number in gold. No other words, no paragraphs, no captions, no fake documents with writing, "
    "no English, no logos, no watermark. Real historical people: never show a recognizable face (use back view, silhouette, "
    "distance, or their objects). Maps: simple stylized silhouette with at most two place labels. "
    "Keep the bottom 20% of the frame free of any text (subtitles go there)."
)


def slide_prompt(sc: dict) -> str:
    number = f"\nKey number (exactly): {sc['slide_number']}" if sc.get("slide_number") else ""
    return (f"{SLIDE_STYLE}\n\nNarration (context only, do not write it on the slide):\n{sc['script_text']}\n\n"
            f"Headline (exactly): {sc.get('slide_headline') or sc['heading']}{number}\nWhat to show: {sc['visual_description']}")


SLIDE_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "headline_correct": {"type": "boolean"},
                    "number_correct": {"type": "boolean"},
                    "extra_or_garbled_text": {"type": "boolean"},
                    "recognizable_real_person_face": {"type": "boolean"},
                    "wrong_place_or_era": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["index", "headline_correct", "number_correct", "extra_or_garbled_text",
                             "recognizable_real_person_face", "wrong_place_or_era", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}
SLIDE_CHECK_CHUNK = 20


def check_slides(p, scenes: dict[str, dict], slides: dict[str, ImageResult], episode_id: int) -> set[str]:
    """投影片上的字由生圖模型寫，逐張比對大標與數字，並檢查亂碼、真人臉孔、時代地點錯誤；回傳不合格的場景。"""
    from PIL import Image

    if not slides or not hasattr(p.llm, "vision_json"):
        return set()
    st = get_storage()
    bad: set[str] = set()
    ids = list(slides)
    for i in range(0, len(ids), SLIDE_CHECK_CHUNK):
        chunk = ids[i:i + SLIDE_CHECK_CHUNK]
        images = []
        for sid in chunk:
            small = st.path(episode_id, "assets", f"{sid}_check.jpg")
            Image.open(slides[sid].path).convert("RGB").resize((768, 432)).save(small, quality=85)
            images.append(small)
        listing = "\n".join(f"{j}: 大標「{scenes[sid].get('slide_headline') or scenes[sid]['heading']}」"
                            f"{'，數字「' + scenes[sid]['slide_number'] + '」' if scenes[sid].get('slide_number') else '，無數字（number_correct 填 true）'}"
                            f"；旁白：{scenes[sid]['script_text']}" for j, sid in enumerate(chunk))
        v = p.llm.vision_json(
            "slide_check", "你是紀錄片的畫面審核員，只依圖片實際內容判斷。",
            "依序檢查以下投影片（index 從 0 開始）。headline_correct：圖上大標是否與指定文字逐字相同；number_correct：數字是否逐字相同；"
            "extra_or_garbled_text：是否出現指定以外的文字或亂碼；recognizable_real_person_face：是否畫出可辨識的真實歷史人物臉孔；"
            "wrong_place_or_era：畫面時代或地點是否明顯與旁白不符。\n\n" + listing,
            images, SLIDE_CHECK_SCHEMA, episode_id,
        )
        for res in v["results"]:
            if 0 <= res["index"] < len(chunk) and (not res["headline_correct"] or not res["number_correct"] or res["extra_or_garbled_text"]
                                                   or res["recognizable_real_person_face"] or res["wrong_place_or_era"]):
                bad.add(chunk[res["index"]])
                log.info("slide_rejected", extra={"episode_id": episode_id, "scene": chunk[res["index"]], "note": res["note"][:200]})
    return bad


def make_slides(p, pending: list[dict], episode_id: int) -> dict[str, ImageResult]:
    """整集投影片：生成 → 審核 → 不合格的重做一次 → 再審，仍不合格的交給備用素材。"""
    st = get_storage()
    by_id = {sc["scene_id"]: sc for sc in pending}
    slides = p.slides.generate_many({sid: (slide_prompt(sc), st.path(episode_id, "assets", sid)) for sid, sc in by_id.items()},
                                    episode_id)
    bad = check_slides(p, by_id, slides, episode_id)
    if bad:
        redo = p.slides.generate_many({sid: (slide_prompt(by_id[sid]), st.path(episode_id, "assets", sid)) for sid in bad}, episode_id)
        still = check_slides(p, by_id, redo, episode_id)
        for sid in bad:
            slides.pop(sid, None)
            if sid in redo and sid not in still:
                slides[sid] = redo[sid]
    return slides


FALLBACKS = {
    "slide": [_video, _stock, _archive],
    "stock_video": [_video, _stock, _archive],
    "stock_photo": [_stock, _video, _archive],
    "archive_image": [_archive, _stock],
    "ai_image": [_ai, _archive, _stock],
}


def acquire(p, sc: dict, out: Path, episode_id: int, hero: Path | None, slides: dict | None = None) -> ImageResult:
    vt = sc["visual_type"]
    if vt == "slide" and slides and sc["scene_id"] in slides:
        return slides[sc["scene_id"]]
    if vt == "chart" and sc.get("chart", {}).get("values"):
        cards.chart(sc["chart"], out.with_suffix(".png"))
        return ImageResult(out.with_suffix(".png"), source=f"original:chart:{sc['chart'].get('source', '')}",
                           creator="世界先修課", license="Original", usage_rights="owned")
    if vt == "map":
        cards.map_card(sc.get("map_place") or sc["heading"], sc.get("map_caption", ""), out.with_suffix(".jpg"), hero)
        return ImageResult(out.with_suffix(".jpg"), source="original:map_card", creator="世界先修課",
                           license="Original", usage_rights="owned")
    if vt == "title_card":
        return _card(sc, out)
    for fn in FALLBACKS.get(vt, [_stock, _archive]):
        try:
            r = fn(p, sc, out, episode_id)
        except Exception as e:
            log.warning("visual_provider_failed", extra={"episode_id": episode_id, "scene": sc["scene_id"], "err": str(e)[:300]})
            r = None
        if r:
            return r
    return _card(sc, out)


def run(p, episode_id: int, only_scenes: set[str] | None = None) -> None:
    st = get_storage()
    scenes = st.read_json(episode_id, "scripts", "storyboard.json")
    manifest_name = "manifest.json"
    manifest = st.read_json(episode_id, "assets", manifest_name) if st.exists(episode_id, "assets", manifest_name) else {}
    if only_scenes:
        for sid in only_scenes:
            manifest.pop(sid, None)
    pending = [sc for sc in scenes if sc["visual_type"] == "slide"
               and not (manifest.get(sc["scene_id"]) and Path(manifest[sc["scene_id"]]["file_path"]).exists())]
    slides = make_slides(p, pending, episode_id) if p.slides and pending else {}
    hero = None
    for sc in scenes:
        sid = sc["scene_id"]
        entry = manifest.get(sid)
        if entry and Path(entry["file_path"]).exists():
            if not hero and entry["source"].startswith("http") and not entry["ai_generated"]:
                hero = Path(entry["poster"])
            continue
        r = acquire(p, sc, st.path(episode_id, "assets", sid), episode_id, hero, slides)
        poster = poster_frame(r.path, st.path(episode_id, "assets", f"{sid}_poster.jpg")) if r.media_type == "video" else r.path
        if not hero and not r.ai_generated and r.source.startswith("http"):
            hero = poster
        overlay = cards.text_overlay(sc.get("on_screen_text", ""), st.path(episode_id, "assets", f"{sid}_overlay.png"))
        manifest[sid] = {"file_path": str(r.path), "overlay": str(overlay), "source": r.source, "creator": r.creator,
                         "license": r.license, "license_url": r.license_url, "usage_rights": r.usage_rights,
                         "attribution_required": r.attribution_required, "ai_generated": r.ai_generated,
                         "realistic": r.realistic, "asset_type": sc["visual_type"], "media_type": r.media_type,
                         "poster": str(poster)}
        st.write_json(episode_id, "assets", manifest_name, manifest)

    with session() as s:
        s.execute(delete(Asset).where(Asset.episode_id == episode_id, Asset.asset_type != "music"))
        for sid, m in manifest.items():
            s.add(Asset(episode_id=episode_id, scene_id=sid, asset_type=m["asset_type"], source=m["source"],
                        creator=m["creator"], license=m["license"], license_url=m["license_url"],
                        usage_rights=m["usage_rights"], attribution_required=m["attribution_required"],
                        ai_generated=m["ai_generated"], realistic=m["realistic"], file_path=m["file_path"]))
        s.get(Episode, episode_id).contains_synthetic_media = any(m["ai_generated"] and m["realistic"] for m in manifest.values())
