"""英文版（Beyond Travel）：沿用中文版的研究、查核與大部分投影片，改寫英文旁白、重做有字的畫面、英文配音、封面、字幕、短影音與文案。"""
import copy
import html
import shutil
from pathlib import Path

from sqlalchemy import select

from .. import edition
from ..brand import ep_label
from ..config import get_settings
from ..db import session
from ..logging_setup import log
from ..models import Asset, Episode, ResearchSource
from ..render import cards
from ..storage import get_storage
from . import edit, shorts, thumbnail, voice
from .metadata import _fmt, chapters
from .script import hook_char_limit
from .visual import check_slides, slide_prompt

EN = edition.EN
LINK = "[YouTube video link]"
CHANNEL_DESCRIPTION_EN = (
    "Beyond Travel explains why places became what they are, through history, cities, business, culture and the sights "
    "you will see. Understand the world before you go."
)

ADAPT_SCHEMA = {
    "type": "object",
    "properties": {
        "destination": {"type": "string"},
        "titles": {"type": "array", "items": {"type": "string"}},
        "scenes": {"type": "array", "items": {"type": "object", "properties": {"scene_id": {"type": "string"}, "text": {"type": "string"}},
                                              "required": ["scene_id", "text"], "additionalProperties": False}},
        "headings": {"type": "array", "items": {"type": "object", "properties": {"section": {"type": "string"}, "heading": {"type": "string"}},
                                                "required": ["section", "heading"], "additionalProperties": False}},
        "slide_headlines": {"type": "array", "items": {"type": "object", "properties": {
            "scene_id": {"type": "string"}, "headline": {"type": "string"}}, "required": ["scene_id", "headline"], "additionalProperties": False}},
        "charts": {"type": "array", "items": {"type": "object", "properties": {
            "scene_id": {"type": "string"}, "title": {"type": "string"}, "unit": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}}},
            "required": ["scene_id", "title", "unit", "labels"], "additionalProperties": False}},
    },
    "required": ["destination", "titles", "scenes", "headings", "slide_headlines", "charts"],
    "additionalProperties": False,
}

META_SCHEMA = {
    "type": "object",
    "properties": {
        "description_intro": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "pinned_comment": {"type": "string"},
        "shorts_title": {"type": "string"},
        "shorts_description": {"type": "string"},
        "instagram_caption": {"type": "string"},
        "threads_post": {"type": "string"},
    },
    "required": ["description_intro", "tags", "hashtags", "pinned_comment", "shorts_title", "shorts_description",
                 "instagram_caption", "threads_post"],
    "additionalProperties": False,
}

DELIVERABLES = [("final", "episode.mp4"), ("final", "short.mp4"), ("thumbnails", "short_cover.jpg"), ("thumbnails", "thumbnail.jpg"),
                ("subtitles", "en.srt"), ("final", "metadata.json"), ("scripts", "script.txt")]


def en_root(episode_id: int) -> Path:
    return get_settings().storage_root / f"ep{episode_id:04d}" / "en"


def full_title(title: str, n: int) -> str:
    return f"{title.strip()} | {EN.channel} {ep_label(n)}"


def adapt(p, episode_id: int, scenes: list[dict], zh_title: str, dest: str) -> dict:
    """改寫成英文旁白：每個場景對應一句英文，畫面才能沿用。"""
    listing = "\n".join(f"{sc['scene_id']} [{sc['section']}] {sc['script_text']}" for sc in scenes)
    headlines = "\n".join(f"{sc['scene_id']}: {sc['slide_headline']}" for sc in scenes if sc.get("slide_headline"))
    charts = "\n".join(f"{sc['scene_id']}: title={sc['chart'].get('title')} unit={sc['chart'].get('unit')} labels={sc['chart'].get('labels')}"
                       for sc in scenes if sc["visual_type"] == "chart" and sc.get("chart", {}).get("values"))
    sections = sorted({(sc["section"], sc["heading"]) for sc in scenes}, key=lambda x: [s["section"] for s in scenes].index(x[0]))
    words = round(hook_char_limit() / 1.9)  # 中文字數換算英文字數（約 15–23 秒）
    return p.llm.json(
        "en_script",
        "You are the head writer of Beyond Travel, an English YouTube documentary channel that explains why places became what "
        "they are. Your viewers are curious international English speakers who know almost nothing about the place.",
        f"Adapt this Chinese narration of an episode about {dest} (Chinese title: {zh_title}) into natural, engaging spoken English.\n"
        "Rules:\n"
        "- One English text per scene_id, in the same order, keeping each scene's meaning so the existing visuals still match.\n"
        "- Write for the ear: storytelling, vivid, conversational, not a literal translation and not a textbook.\n"
        "- Keep every fact, number and date exactly as given. Do not add facts. Use standard English spellings of names.\n"
        f"- The hook scenes together must be about {round(words * 0.7)}–{words} words (15–23 seconds).\n"
        "- titles: 3 YouTube titles in English (max 70 characters, no channel name), each built on a concrete, curious hook.\n"
        "- headings: an English heading for each section listed below.\n"
        "- slide_headlines: an English place label (max 4 words) for each listed slide headline.\n"
        "- charts: English title, unit and labels (same order) for each listed chart; keep numbers out of these fields.\n"
        "- destination: the English name of the place.\n\n"
        f"## Scenes\n{listing}\n\n## Sections\n" + "\n".join(f"{s}: {h}" for s, h in sections)
        + (f"\n\n## Slide headlines\n{headlines}" if headlines else "") + (f"\n\n## Charts\n{charts}" if charts else ""),
        ADAPT_SCHEMA, episode_id, effort="high",
    )


def _retext(p, sc: dict, zh_scene: dict, src: Path, out: Path, episode_id: int):
    """把投影片上的中文標籤換成英文（以中文版投影片為參考圖，畫面其餘部分不變）。"""
    s = get_settings()
    number = f' Keep the number "{sc["slide_number"]}" exactly as it is.' if sc.get("slide_number") else ""
    prompt = (f'Edit this image: replace the Chinese text "{zh_scene["slide_headline"]}" with the English text "{sc["slide_headline"]}" '
              f"in the same position, size and style.{number} Keep everything else in the image exactly the same. "
              "No other text, no Chinese characters.")
    return p.slides.compose(prompt, [src], out, episode_id, model=s.slide_model, price=s.slide_price_usd)


def build_assets(p, episode_id: int, scenes: list[dict], zh_scenes: dict, zh_manifest: dict) -> dict:
    st = get_storage()
    manifest = {}
    retexted = {}
    for sc in scenes:
        sid, m = sc["scene_id"], copy.deepcopy(zh_manifest[sc["scene_id"]])
        m["overlay"] = str(cards.text_overlay("", st.path(episode_id, "assets", f"{sid}_overlay.png")))
        if sc["visual_type"] == "chart" and sc.get("chart", {}).get("values"):
            m["file_path"] = m["poster"] = str(cards.chart(sc["chart"], st.path(episode_id, "assets", f"{sid}.png")))
        elif m["source"].startswith("original"):
            m["file_path"] = m["poster"] = str(cards.title_card(sc.get("slide_headline") or sc["heading"], "",
                                                                st.path(episode_id, "assets", f"{sid}.jpg")))
        elif sc.get("slide_headline") and m["source"].startswith("gemini") and p.slides:
            try:
                retexted[sid] = _retext(p, sc, zh_scenes[sid], Path(m["file_path"]), st.path(episode_id, "assets", sid), episode_id)
            except Exception as e:
                log.warning("en_retext_failed", extra={"episode_id": episode_id, "scene": sid, "err": str(e)[:300]})
                retexted[sid] = None
        manifest[sid] = m
    by_id = {sc["scene_id"]: sc for sc in scenes}
    bad = check_slides(p, by_id, {k: v for k, v in retexted.items() if v}, episode_id) | {k for k, v in retexted.items() if not v}
    for sid, r in retexted.items():
        if sid in bad:
            # 換字失敗：重新生成一張英文版；再不行生成無字版本；都不行就用英文字卡，絕不留下中文字
            r = None
            for sc in (by_id[sid], {**by_id[sid], "slide_headline": "", "slide_number": ""}):
                try:
                    cand = p.slides.get(slide_prompt(sc), st.path(episode_id, "assets", f"{sid}_new"), episode_id)
                except Exception:
                    cand = None
                if cand and not check_slides(p, {sid: sc}, {sid: cand}, episode_id):
                    r = cand
                    break
        path = r.path if r else cards.title_card(by_id[sid]["slide_headline"], "", st.path(episode_id, "assets", f"{sid}_card.jpg"))
        manifest[sid]["file_path"] = manifest[sid]["poster"] = str(path)
    return manifest


def build_metadata(p, episode_id: int, title: str, titles: list[str], dest: str, n: int) -> dict:
    st = get_storage()
    timeline = st.read_json(episode_id, "video", "timeline.json")
    hook = " ".join(sc["text"] for sc in timeline["scenes"] if sc["section"] == "hook")
    m = p.llm.json(
        "en_meta", "You write YouTube metadata and social copy for Beyond Travel, an English documentary channel about why places "
        "became what they are. Every piece of copy should make people want to watch the full YouTube video.",
        f"Title: {title}\nPlace: {dest}\nShort video opening narration: {hook}\n\n## Narration\n"
        + st.path(episode_id, "scripts", "script.txt").read_text(encoding="utf-8")
        + "\n\nWrite, in English, using only facts in the narration, no spoilers of the answer:\n"
        "- description_intro: 2–3 short paragraphs on what the viewer will understand.\n"
        "- tags: 15–25 YouTube tags. hashtags: 3, including #BeyondTravel. pinned_comment: one question that sparks discussion.\n"
        "- shorts_title: under 60 characters, ends with #Shorts.\n"
        f"- shorts_description: 2–3 sentences pointing to the full video, with {LINK} and 3 hashtags including #BeyondTravel.\n"
        "- instagram_caption: hook in the first line, 3–5 short lines, a few emoji at most, say 'Full video: link in bio' "
        "(Instagram captions can't hold clickable links), end with 5–8 hashtags including #BeyondTravel.\n"
        f"- threads_post: under 300 characters, like sharing a surprising fact with a friend, ending with an invitation to "
        f"watch the full video and {LINK}; at most 1 hashtag.",
        META_SCHEMA, episode_id, effort="low",
    )
    with session() as s:
        srcs = list(s.scalars(select(ResearchSource).where(ResearchSource.episode_id == episode_id,
                                                             ResearchSource.verdict.in_(["verified", "qualified"]),
                                                             ResearchSource.source_url != "")))
        attributions = list(s.scalars(select(Asset).where(Asset.episode_id == episode_id, Asset.attribution_required)))
    parts = [m["description_intro"].strip()]
    chs = chapters(timeline)
    if chs:
        parts.append("Chapters\n" + "\n".join(f"{_fmt(t)} {'Intro' if i == 0 else h}" for i, (t, h) in enumerate(chs)))
    urls = list(dict.fromkeys(r.source_url for r in srcs))
    if urls:
        parts.append("Sources\n" + "\n".join(f"- {u}" for u in urls[:15]))
    if attributions:
        parts.append("Credits\n" + "\n".join(f"- {a.creator} / {a.source} ({a.license})" for a in attributions))
    parts.append("Some historical scenes in this video are AI-generated illustrations, not real footage.")
    parts.append(CHANNEL_DESCRIPTION_EN)
    parts.append(" ".join(h if h.startswith("#") else f"#{h}" for h in m["hashtags"]))
    tags, total = [], 0
    for tag in m["tags"]:
        if total + len(tag) + 2 > 480:
            break
        tags.append(tag)
        total += len(tag) + 2
    out = {"title": full_title(title, n), "main_title": title, "title_alternates": [full_title(t, n) for t in titles[1:3]],
           "description": "\n\n".join(parts).replace("<", "＜").replace(">", "＞")[:4900], "tags": tags,
           "pinned_comment": m["pinned_comment"], "destination": dest,
           "social": {k: m[k] for k in ("shorts_title", "shorts_description", "instagram_caption", "threads_post")}}
    st.write_json(episode_id, "final", "metadata.json", out)
    return out


def run(p, episode_id: int) -> Path | None:
    """回傳英文版工作目錄；已完成就直接回傳。"""
    zh = get_storage()
    root = en_root(episode_id)
    if (root / f"ep{episode_id:04d}" / "final" / "metadata.json").exists():
        return root
    zh_scenes = zh.read_json(episode_id, "scripts", "storyboard.json")
    zh_manifest = zh.read_json(episode_id, "assets", "manifest.json")
    brief = zh.path(episode_id, "plan", "brief.json")
    with session() as s:
        ep = s.get(Episode, episode_id)
        n, zh_title, zh_dest = ep.episode_number, ep.title, ep.destination
    with edition.use(EN, episode_id):
        st = get_storage()
        if brief.exists():
            shutil.copy(brief, st.path(episode_id, "plan", "brief.json"))
        if st.exists(episode_id, "scripts", "adapt.json"):
            a = st.read_json(episode_id, "scripts", "adapt.json")
        else:
            a = adapt(p, episode_id, zh_scenes, zh_title, zh_dest)
            st.write_json(episode_id, "scripts", "adapt.json", a)
        text = {x["scene_id"]: x["text"] for x in a["scenes"]}
        heads = {x["section"]: x["heading"] for x in a["headings"]}
        labels = {x["scene_id"]: x["headline"] for x in a["slide_headlines"]}
        charts = {x["scene_id"]: x for x in a["charts"]}
        scenes = []
        for sc in zh_scenes:
            e = copy.deepcopy(sc)
            sid = sc["scene_id"]
            e["script_text"] = text.get(sid, "")
            e["heading"] = heads.get(sc["section"], sc["section"].title())
            e["slide_headline"] = labels.get(sid, "") if sc.get("slide_headline") else ""
            if sid in charts:
                e["chart"] = {**sc["chart"], "title": charts[sid]["title"], "unit": charts[sid]["unit"],
                              "labels": charts[sid]["labels"] or sc["chart"].get("labels", [])}
            scenes.append(e)
        missing = [sc["scene_id"] for sc in scenes if not sc["script_text"]]
        if missing:
            raise RuntimeError(f"英文改寫缺少場景：{missing[:10]}")
        st.write_json(episode_id, "scripts", "storyboard.json", scenes)
        st.write_text(episode_id, "scripts", "script.txt", "\n\n".join(sc["script_text"] for sc in scenes))
        if not st.exists(episode_id, "assets", "manifest.json"):
            st.write_json(episode_id, "assets", "manifest.json", build_assets(p, episode_id, scenes, {s["scene_id"]: s for s in zh_scenes}, zh_manifest))
        voice.run(p, episode_id)
        title = a["titles"][0]
        thumbnail.run(p, episode_id, title=full_title(title, n))
        edit.run(p, episode_id)
        shorts.run(p, episode_id)
        build_metadata(p, episode_id, title, a["titles"], a["destination"], n)
    log.info("english_done", extra={"episode_id": episode_id})
    return root


def email(eid: int) -> tuple[str, str, str, Path] | None:
    """英文版通知信（主旨、HTML、純文字、封面圖）；英文版沒做好就回傳 None。"""
    root = en_root(eid) / f"ep{eid:04d}"
    meta_path = root / "final" / "metadata.json"
    if not meta_path.exists():
        return None
    import json

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    delivery = root / "final" / "delivery.txt"
    folder = delivery.read_text(encoding="utf-8").strip() if delivery.exists() else ""
    with session() as s:
        ep = s.get(Episode, eid)
        n = ep.episode_number
    social = meta.get("social", {})
    box = "background:#f5f3ee;border:1px solid #e0dccf;border-radius:6px;padding:14px;white-space:pre-wrap;font-size:14px;line-height:1.6"
    sections = [("YouTube title", meta["title"]), ("Alternative titles", "\n".join(meta.get("title_alternates", []))),
                ("YouTube description", meta["description"]), ("Tags", ", ".join(meta["tags"])),
                ("Pinned comment (suggested)", meta.get("pinned_comment", "")),
                ("YouTube Shorts title", social.get("shorts_title", "")), ("YouTube Shorts description", social.get("shorts_description", "")),
                ("Instagram post (with thumbnail.jpg)", social.get("instagram_caption", "")),
                ("Threads post (with short.mp4)", social.get("threads_post", ""))]
    sections = [(k, v) for k, v in sections if v]
    checklist = [
        "Upload episode.mp4 to the Beyond Travel channel",
        "Paste the title and description below (or use an alternative title)",
        "Thumbnail: thumbnail.jpg",
        "Subtitles: upload en.srt (English subtitles are already burned in; this file is for CC and search)",
        "Category: Travel & Events; Audience: not made for kids",
        "Altered or synthetic content: Yes (the video contains realistic AI-generated scenes)",
        "Shorts: after the main video is live, upload short.mp4 in the YouTube app, set short_cover.jpg as the thumbnail, "
        "and choose the main video as the Related video",
        f"Social: Instagram with thumbnail.jpg, Threads with short.mp4; replace {LINK} with the video URL",
    ]
    link = f'<p><a href="{html.escape(folder)}">Open the Google Drive folder</a></p>' if folder else ""
    body = (f'<div style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#0b1b3a">'
            f'<div style="background:#0b1b3a;color:#fff;padding:18px 22px"><b style="font-size:19px">{EN.channel} {ep_label(n)} is ready</b><br>'
            f'<span style="color:#d4a853">{EN.brand_lines[1]}</span></div><div style="padding:18px 22px">'
            f'<p style="color:#556;margin-top:0">Place: {html.escape(meta.get("destination", ""))}</p>'
            f'<img src="cid:thumb" width="636" style="width:100%;border-radius:6px" alt="thumbnail">{link}'
            + "".join(f'<h3 style="margin:22px 0 6px;font-size:15px">{html.escape(k)}</h3><div style="{box}">{html.escape(v)}</div>'
                      for k, v in sections)
            + '<h3 style="margin:22px 0 6px;font-size:15px">Upload checklist</h3><ol style="line-height:1.8;padding-left:20px">'
            + "".join(f"<li>{html.escape(x)}</li>" for x in checklist) + "</ol>"
            + '<p style="color:#889;font-size:12px">Topics for the next episodes are chosen by replying to the Chinese (世界先修課) email.</p>'
            "</div></div>")
    text = (f"{EN.channel} {ep_label(n)} is ready\n\nDrive: {folder}\n\n" + "".join(f"[{k}]\n{v}\n\n" for k, v in sections)
            + "[Upload checklist]\n" + "\n".join(f"- {x}" for x in checklist))
    return f"{EN.channel} {ep_label(n)} is ready: {meta['main_title']}", body, text, root / "thumbnails" / "thumbnail.jpg"
