from pathlib import Path

from sqlalchemy import delete

from .. import costs
from ..db import session
from ..logging_setup import log
from ..models import Asset, Episode
from ..providers.base import ImageResult
from ..render import cards, motion
from ..render.ffmpeg import FOCUS, poster_frame
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
    "Create one 16:9 frame for a cinematic documentary on a YouTube channel about why places became what they are. "
    "The frame must clearly show what the narration is talking about, so a viewer could guess the sentence from the image alone. "
    "Exact place and period must be correct; photorealistic, or an authentic archival-photo look for historical periods. "
    "Fill the entire frame edge to edge: no black bars, no letterboxing, no borders or frames. Natural light, rich detail, subtle deep navy (#0b1b3a) and warm gold (#d4a853) grading, consistent across the episode. "
    "No fake documents with writing, no English, no logos, no watermark. Real historical people: never show a recognizable face "
    "(use back view, silhouette, distance, or their objects). Maps: simple stylized silhouette with at most two place labels. "
    "Keep the bottom 20% of the frame free of any text (subtitles go there)."
)


def slide_text(sc: dict) -> str:
    head, number = sc.get("slide_headline", ""), sc.get("slide_number", "")
    if not head and not number:
        return "Text: none. Absolutely no words, letters or numbers anywhere in the image."
    parts = [f"a small elegant place label \"{head}\" in the top-left corner"] if head else []
    if number:
        parts.append(f"the number \"{number}\" in gold")
    return ("Text: show ONLY " + " and ".join(parts) + " (Traditional Chinese as used in Taiwan, exactly as written). "
            "Keep text small so the place stays the focus. No other words.")


SHOT_GUIDE = {
    "people": "Shot type: people in action — a scene of the specific people and activity described, mid-shot, natural candid moment.",
    "object": "Shot type: object close-up — the specific object described fills most of the frame, shallow depth of field, tactile detail.",
    "map": "Shot type: stylized map — a clean, simple illustrated map with the route or area described, minimal labels.",
    "then_now": "Shot type: then-and-now split — the same place in one frame, left half as it looked in the past, right half today, "
                "with a soft vertical blend in the middle.",
    "daily_life": "Shot type: daily life today — ordinary local people going about the activity described, street-level documentary photo.",
    "landmark": "Shot type: landmark — the specific landmark or view described, high-end travel photography.",
}


def slide_prompt(sc: dict) -> str:
    shot = SHOT_GUIDE.get(sc.get("shot_type", ""), "")
    return (f"{SLIDE_STYLE}\n{shot}\n{slide_text(sc)}\n\nNarration (context only, do not write it on the image):\n{sc['script_text']}\n\n"
            f"What to show: {sc['visual_description']}")


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
                    "shows_narration": {"type": "boolean"},
                    "detail_area": {"type": "string", "enum": list(FOCUS)},
                    "note": {"type": "string"},
                },
                "required": ["index", "headline_correct", "number_correct", "extra_or_garbled_text",
                             "recognizable_real_person_face", "wrong_place_or_era", "shows_narration", "detail_area", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}
SLIDE_CHECK_CHUNK = 20


def letterboxed(path: Path) -> bool:
    """生圖偶爾自己加上下黑邊（電影寬銀幕感），在 16:9 影片裡會變成黑框。"""
    from PIL import Image

    im = Image.open(path).convert("L").resize((64, 36))
    row = lambda y: sum(im.getpixel((x, y)) for x in range(64)) / 64  # noqa: E731
    return row(1) < 8 and row(34) < 8


def check_slides(p, scenes: dict[str, dict], slides: dict[str, ImageResult], episode_id: int,
                 focus: dict[str, str] | None = None) -> set[str]:
    """投影片上的字由生圖模型寫，逐張比對大標與數字，並檢查亂碼、真人臉孔、時代地點錯誤、黑邊；回傳不合格的場景。
    focus 有傳入時，順便記下每張圖最有看頭的區域（剪輯「全景 → 特寫」用）。"""
    from PIL import Image

    bad: set[str] = {sid for sid, r in slides.items() if letterboxed(r.path)}
    if not slides or not hasattr(p.llm, "vision_json"):
        return bad
    st = get_storage()
    ids = [sid for sid in slides if sid not in bad]
    for i in range(0, len(ids), SLIDE_CHECK_CHUNK):
        chunk = ids[i:i + SLIDE_CHECK_CHUNK]
        images = []
        for sid in chunk:
            small = st.path(episode_id, "assets", f"{sid}_check.jpg")
            Image.open(slides[sid].path).convert("RGB").resize((768, 432)).save(small, quality=85)
            images.append(small)
        def expected(sc: dict) -> str:
            head = f"地名標籤「{sc['slide_headline']}」" if sc.get("slide_headline") else "應該完全沒有文字（圖上沒有任何字時 headline_correct=true；出現任何字才是 false）"
            num = f"數字「{sc['slide_number']}」" if sc.get("slide_number") else "沒有數字（number_correct 填 true）"
            return f"{head}，{num}"

        listing = "\n".join(f"{j}: {expected(scenes[sid])}；旁白：{scenes[sid]['script_text']}" for j, sid in enumerate(chunk))
        v = p.llm.vision_json(
            "slide_check", "你是紀錄片的畫面審核員，只依圖片實際內容判斷。",
            "依序檢查以下投影片（index 從 0 開始）。headline_correct：圖上的地名標籤是否與指定文字逐字相同（指定沒有標籤時，圖上必須完全沒有字）；number_correct：數字是否逐字相同；"
            "extra_or_garbled_text：是否出現指定以外的文字或亂碼；recognizable_real_person_face：是否畫出可辨識的真實歷史人物臉孔；"
            "wrong_place_or_era：畫面時代或地點是否明顯與旁白不符。"
            "shows_narration：畫面是否畫出旁白在講的具體東西或動作（只是一般風景、和旁白無關時填 false）。"
            "detail_area：畫面中最值得特寫的主體（人物動作、物件細節）在九宮格的哪個位置，放大後仍要完整看得到主體。\n\n" + listing,
            images, SLIDE_CHECK_SCHEMA, episode_id,
        )
        for res in v["results"]:
            if focus is not None and 0 <= res["index"] < len(chunk):
                focus[chunk[res["index"]]] = res.get("detail_area", "center")
            if 0 <= res["index"] < len(chunk) and (not res["headline_correct"] or not res["number_correct"] or res["extra_or_garbled_text"]
                                                   or res["recognizable_real_person_face"] or res["wrong_place_or_era"]
                                                   or not res["shows_narration"]):
                bad.add(chunk[res["index"]])
                log.info("slide_rejected", extra={"episode_id": episode_id, "scene": chunk[res["index"]], "note": res["note"][:200]})
    return bad


def make_slides(p, pending: list[dict], episode_id: int, focus: dict[str, str] | None = None) -> dict[str, ImageResult]:
    """整集投影片：生成 → 審核 → 不合格的重做一次 → 再審，仍不合格的交給備用素材。"""
    st = get_storage()
    by_id = {sc["scene_id"]: sc for sc in pending}
    slides = p.slides.generate_many({sid: (slide_prompt(sc), st.path(episode_id, "assets", sid)) for sid, sc in by_id.items()},
                                    episode_id)
    bad = check_slides(p, by_id, slides, episode_id, focus)
    if bad:
        redo = p.slides.generate_many({sid: (slide_prompt(by_id[sid]), st.path(episode_id, "assets", sid)) for sid in bad}, episode_id)
        still = check_slides(p, by_id, redo, episode_id, focus)
        for sid in bad:
            slides.pop(sid, None)
            if sid in redo and sid not in still:
                slides[sid] = redo[sid]
    return slides


FOOTAGE_CHECK_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer"}, "fits": {"type": "boolean"}, "note": {"type": "string"}},
        "required": ["index", "fits", "note"], "additionalProperties": False}}},
    "required": ["results"],
    "additionalProperties": False,
}


def find_footage(p, scenes: list[dict], episode_id: int) -> dict[str, ImageResult]:
    """stock_video 場景：到 Pixabay 找免費實拍影片，再看截圖確認是對的城市、和旁白相符；找不到或不合格的回傳時不包含，交給投影片。"""
    st = get_storage()
    found: dict[str, ImageResult] = {}
    for sc in scenes:
        try:
            r = _video(p, sc, st.path(episode_id, "assets", sc["scene_id"]), episode_id)
        except Exception as e:
            log.warning("footage_failed", extra={"episode_id": episode_id, "scene": sc["scene_id"], "err": str(e)[:300]})
            r = None
        if r:
            found[sc["scene_id"]] = r
    if not found or not hasattr(p.llm, "vision_json"):
        return found
    ids = list(found)
    posters = [poster_frame(found[sid].path, st.path(episode_id, "assets", f"{sid}_check.jpg")) for sid in ids]
    by_id = {sc["scene_id"]: sc for sc in scenes}
    listing = "\n".join(f"{j}: 關鍵字「{by_id[sid]['search_query']}」；旁白：{by_id[sid]['script_text']}" for j, sid in enumerate(ids))
    v = p.llm.vision_json(
        "slide_check", "你是紀錄片的畫面審核員，只依圖片實際內容判斷。",
        "以下是免費圖庫實拍影片的截圖（index 從 0 開始）。fits：畫面看起來是關鍵字裡的那個城市或同一國家的同類場景、"
        "和旁白講的東西相符、沒有明顯浮水印或大段文字時填 true；地點明顯不對、和旁白無關時填 false。\n\n" + listing,
        posters, FOOTAGE_CHECK_SCHEMA, episode_id,
    )
    ok = {ids[x["index"]] for x in v["results"] if 0 <= x["index"] < len(ids) and x["fits"]}
    for sid in set(ids) - ok:
        log.info("footage_rejected", extra={"episode_id": episode_id, "scene": sid})
        found[sid].path.unlink(missing_ok=True)
    return {sid: r for sid, r in found.items() if sid in ok}


FALLBACKS = {
    "slide": [_video, _stock, _archive],
    "stock_video": [_video, _stock, _archive],
    "stock_photo": [_stock, _video, _archive],
    "archive_image": [_archive, _stock],
    "ai_image": [_ai, _archive, _stock],
}


def acquire(p, sc: dict, out: Path, episode_id: int, hero: Path | None, slides: dict | None = None) -> ImageResult:
    vt = sc["visual_type"]
    if vt in ("slide", "stock_video") and slides and sc["scene_id"] in slides:
        return slides[sc["scene_id"]]
    if vt == "outline":
        k = sc.get("outline_reveal", len(sc.get("outline_points", [])))
        cards.outline_card(sc["heading"], sc.get("outline_points", []), out.with_suffix(".jpg"), shown=max(0, k - 1))
        return ImageResult(out.with_suffix(".jpg"), source="original:outline", creator="世界先修課",
                           license="Original", usage_rights="owned")
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
    todo = [sc for sc in scenes if not (manifest.get(sc["scene_id"]) and Path(manifest[sc["scene_id"]]["file_path"]).exists())]
    footage = find_footage(p, [sc for sc in todo if sc["visual_type"] == "stock_video"], episode_id) if p.stock_videos else {}
    # 找不到合適實拍影片的場景改做投影片
    pending = [sc for sc in todo if sc["visual_type"] == "slide" or (sc["visual_type"] == "stock_video" and sc["scene_id"] not in footage)]
    focus: dict[str, str] = {}
    slides = make_slides(p, pending, episode_id, focus) if p.slides and pending else {}
    slides.update(footage)
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
                         "poster": str(poster), "focus": focus.get(sid, "center")}
        if sc["visual_type"] == "outline" and 1 <= sc.get("outline_reveal", 0) <= len(sc.get("outline_points", [])[:4]):
            manifest[sid]["outline_item"] = str(cards.outline_item(sc["outline_points"], sc["outline_reveal"],
                                                                   st.path(episode_id, "assets", f"{sid}_item.png")))
        if sc.get("shot_type") == "map" and sc.get("map_points"):
            # 動態地圖用的真實座標（剪輯時由 HyperFrames 渲染；失敗就用上面的 AI 地圖）
            manifest[sid]["map"] = {"route": bool(sc.get("map_route")), "points": [
                {**pt, **dict(zip(("lat", "lon"), motion.geocode(pt["query"], (pt["lat"], pt["lon"]))))}
                for pt in sc["map_points"][:4]]}
        st.write_json(episode_id, "assets", manifest_name, manifest)

    with session() as s:
        s.execute(delete(Asset).where(Asset.episode_id == episode_id, Asset.asset_type != "music"))
        for sid, m in manifest.items():
            s.add(Asset(episode_id=episode_id, scene_id=sid, asset_type=m["asset_type"], source=m["source"],
                        creator=m["creator"], license=m["license"], license_url=m["license_url"],
                        usage_rights=m["usage_rights"], attribution_required=m["attribution_required"],
                        ai_generated=m["ai_generated"], realistic=m["realistic"], file_path=m["file_path"]))
        s.get(Episode, episode_id).contains_synthetic_media = any(m["ai_generated"] and m["realistic"] for m in manifest.values())
