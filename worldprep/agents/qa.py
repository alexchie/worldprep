from itertools import groupby

from sqlalchemy import select

from ..brand import validate_title
from ..config import get_settings
from ..db import session
from ..models import Asset, Episode, ResearchSource
from ..render.ffmpeg import probe, run_ffmpeg, volume_stats
from ..storage import get_storage
from .prompts import EDITORIAL_DNA
from .script import SECTION_ORDER, structural_issues

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "frames": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "matches_narration": {"type": "boolean"},
                    "ai_artifacts": {"type": "boolean"},
                    "text_legible": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["index", "matches_narration", "ai_artifacts", "text_legible", "note"],
                "additionalProperties": False,
            },
        },
        "thumbnail_on_brand": {"type": "boolean"},
        "thumbnail_note": {"type": "string"},
    },
    "required": ["frames", "thumbnail_on_brand", "thumbnail_note"],
    "additionalProperties": False,
}


def _check(results: list, group: str, name: str, ok: bool, detail: str = "", critical: bool = True) -> None:
    results.append({"group": group, "check": name, "pass": bool(ok), "detail": detail, "critical": critical})


def run(p, episode_id: int) -> bool:
    st = get_storage()
    cfg = get_settings()
    r: list[dict] = []
    with session() as s:
        ep = s.get(Episode, episode_id)
        n = ep.episode_number
        assets = list(s.scalars(select(Asset).where(Asset.episode_id == episode_id)))
        claim_text = {r_.id: r_.claim for r_ in s.scalars(select(ResearchSource).where(
            ResearchSource.episode_id == episode_id, ResearchSource.verdict.in_(["verified", "qualified"])))}
        valid_ids = set(claim_text)
    script = st.read_json(episode_id, "scripts", "script.json")
    review = st.read_json(episode_id, "scripts", "review.json") if st.exists(episode_id, "scripts", "review.json") else None
    scenes = st.read_json(episode_id, "scripts", "storyboard.json")
    meta = st.read_json(episode_id, "final", "metadata.json")
    timeline = st.read_json(episode_id, "video", "timeline.json")
    final = st.path(episode_id, "final", "episode.mp4")
    srt = st.path(episode_id, "subtitles", "zh-Hant.srt")
    thumb = st.path(episode_id, "thumbnails", "thumbnail.jpg")

    # Content / Writing（總字數與 hook 長度改由實際片長、開場時間檢查，非致命）
    issues = [i for i in structural_issues(script, cfg.target_chars, valid_ids) if "總字數" not in i and not i.startswith(("hook ", "大綱"))]
    _check(r, "content", "story_structure", not issues, "; ".join(issues))
    order = [x["section"] for x in script["sections"]]
    _check(r, "content", "section_order", [x for x in SECTION_ORDER if x in order] == order and "hook" in order, critical=False)
    if review:
        _check(r, "content", "coherent", review["coherent"], critical=False)
        _check(r, "content", "hook_strong", review["hook_strong"], critical=False)
        _check(r, "content", "no_unsupported_claims", not review["unsupported_sentences"], "; ".join(review["unsupported_sentences"])[:500],
               critical=False)
        _check(r, "writing", "natural_traditional_chinese", review["natural_taiwanese_chinese"], critical=False)
        _check(r, "writing", "not_ai_sounding", not review["sounds_ai_generated"], critical=False)
    _check(r, "content", "facts_verified", len(valid_ids) >= 5, f"{len(valid_ids)} verified claims")

    # Visual
    types = [sc.get("shot_type") or sc["visual_type"] for sc in scenes]
    longest = max((len(list(g)) for _, g in groupby(types)), default=0)
    landmark = types.count("landmark") / max(1, len(types))
    _check(r, "visual", "visual_variety", longest <= 3 and landmark <= 0.35,
           f"最長連續同類鏡頭 {longest}，地標風景占 {landmark:.0%}", critical=False)
    files = [a.file_path for a in assets if a.asset_type != "music"]
    dup = len(files) - len(set(files))
    _check(r, "visual", "no_duplicate_assets", dup == 0, f"{dup} duplicates", critical=False)
    unlicensed = [a.scene_id or a.file_path for a in assets if not a.license]
    _check(r, "visual", "all_assets_licensed", not unlicensed, ", ".join(unlicensed))
    for sc in scenes:
        ch = sc.get("chart") or {}
        if sc["visual_type"] != "chart" or not ch.get("values"):
            continue
        if ch.get("claim_id") not in valid_ids:
            _check(r, "visual", f"chart_{sc['scene_id']}_verified", False, "圖表數據未對應已查核事實")
            continue
        text = claim_text[ch["claim_id"]].replace(",", "")
        missing = [v for v in ch["values"] if (str(int(v)) if float(v).is_integer() else str(v)) not in text]
        _check(r, "visual", f"chart_{sc['scene_id']}_values_match_claim", not missing,
               f"圖表數值不在引用事實中：{missing}", critical=False)

    # Technical
    info = probe(final) if final.exists() else {}
    _check(r, "technical", "file_exists", final.exists())
    _check(r, "technical", "resolution_1080p", (info.get("width"), info.get("height")) == (1920, 1080), str((info.get("width"), info.get("height"))))
    _check(r, "technical", "frame_rate_30", abs(info.get("fps", 0) - 30) < 0.5, str(info.get("fps")))
    _check(r, "technical", "has_audio", info.get("has_audio", False))
    dur = info.get("duration", 0)
    target = cfg.target_video_length_minutes * 60
    # 只有明顯壞掉（不到目標一半）才擋下交付；不在 8–10 分鐘內則在通知信提醒
    _check(r, "technical", "duration_not_broken", dur >= target * 0.5 or cfg.mock, f"{dur:.0f}s", critical=not cfg.mock)
    _check(r, "technical", "duration_in_range", cfg.video_min_minutes * 60 <= dur <= cfg.video_max_minutes * 60 or cfg.mock,
           f"片長 {dur / 60:.1f} 分鐘，目標 {cfg.video_min_minutes:.0f}–{cfg.video_max_minutes:.0f} 分鐘", critical=False)
    _check(r, "technical", "subtitles", srt.exists() and srt.stat().st_size > 100)

    # Audio
    if final.exists():
        vol = volume_stats(final)
        _check(r, "audio", "no_clipping", vol["max_db"] is not None and vol["max_db"] < -0.1, f"peak {vol['max_db']} dB")
        _check(r, "audio", "narration_audible", vol["mean_db"] is not None and vol["mean_db"] > -35, f"mean {vol['mean_db']} dB",
               critical=not cfg.mock)
    _check(r, "audio", "sync", abs(dur - timeline["total"]) < 1.5, f"video {dur:.1f}s / timeline {timeline['total']:.1f}s")

    # Branding
    errs = validate_title(meta["title"], n)
    _check(r, "branding", "title_format", not errs, "; ".join(errs))
    hook_end, brand_end = timeline.get("hook_end", 0.0), timeline.get("brand_end", 0.0)
    _check(r, "branding", "hook_within_limit", cfg.hook_min_seconds - 1 <= hook_end <= cfg.hook_max_seconds + 1.5,
           f"Hook（含停頓）{hook_end:.1f}s，規範 {cfg.hook_min_seconds:.0f}–{cfg.hook_max_seconds:.0f}s", critical=False)
    _check(r, "branding", "brand_intro", brand_end > hook_end, f"品牌 {hook_end:.1f}–{brand_end:.1f}s", critical=False)
    _check(r, "branding", "thumbnail_exists", thumb.exists() and thumb.stat().st_size < 2 * 1024 * 1024)

    # Vision review (frames vs narration, AI artifacts, thumbnail)
    if not cfg.mock and final.exists() and hasattr(p.llm, "vision_json"):
        picks = timeline["scenes"][:: max(1, len(timeline["scenes"]) // 8)][:8]
        frames = []
        for i, sc in enumerate(picks):
            fp = st.path(episode_id, "video", f"qa_frame_{i}.jpg")
            run_ffmpeg(["-ss", f"{sc['start'] + sc['duration'] / 2:.2f}", "-i", str(final), "-frames:v", "1", "-vf", "scale=960:-2", str(fp)])
            frames.append(fp)
        prompt = "依序檢查以下影格（index 從 0 開始）與對應旁白是否相符、是否有明顯 AI 生成瑕疵（扭曲的手、亂碼文字、錯誤地標）、字卡是否可讀。最後一張是縮圖，檢查是否符合品牌（深藍、金色、白字、單一主體、EP 徽章、不雜亂）。\n\n" + \
                 "\n".join(f"{i}: {sc['text']}" for i, sc in enumerate(picks))
        v = p.llm.vision_json("qa_vision", EDITORIAL_DNA, prompt, frames + [thumb], VISION_SCHEMA, episode_id)
        flagged = (any(f["ai_artifacts"] or not f["matches_narration"] for f in v["frames"]) or not v["thumbnail_on_brand"])
        if flagged:
            # Haiku 做清單式初檢；有疑慮才升級給 Opus 複核，避免誤報或漏報
            v = p.llm.vision_json("qa_vision_escalate", EDITORIAL_DNA, prompt, frames + [thumb], VISION_SCHEMA, episode_id)
        bad = [f for f in v["frames"] if f["ai_artifacts"]]
        mismatch = [f for f in v["frames"] if not f["matches_narration"]]
        _check(r, "visual", "no_ai_artifacts", not bad, "; ".join(f["note"] for f in bad), critical=False)
        _check(r, "visual", "visual_matches_narration", len(mismatch) <= 1, "; ".join(f["note"] for f in mismatch), critical=False)
        _check(r, "branding", "thumbnail_on_brand", v["thumbnail_on_brand"], v["thumbnail_note"], critical=False)

    passed = all(c["pass"] for c in r if c["critical"])
    report = {"passed": passed, "checks": r}
    st.write_json(episode_id, "final", "qa_report.json", report)
    with session() as s:
        ep = s.get(Episode, episode_id)
        ep.qa_status = "PASS" if passed else "FAIL"
        ep.qa_report = report
        if any(not c["pass"] for c in r):
            ep.needs_human_review = ep.needs_human_review or not passed
    return passed
