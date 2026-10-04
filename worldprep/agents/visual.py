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


FALLBACKS = {
    "stock_video": [_video, _stock, _archive],
    "stock_photo": [_stock, _video, _archive],
    "archive_image": [_archive, _stock],
    "ai_image": [_ai, _archive, _stock],
}


def acquire(p, sc: dict, out: Path, episode_id: int, hero: Path | None) -> ImageResult:
    vt = sc["visual_type"]
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
    hero = None
    for sc in scenes:
        sid = sc["scene_id"]
        entry = manifest.get(sid)
        if entry and Path(entry["file_path"]).exists():
            if not hero and entry["source"].startswith("http") and not entry["ai_generated"]:
                hero = Path(entry["poster"])
            continue
        r = acquire(p, sc, st.path(episode_id, "assets", sid), episode_id, hero)
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
