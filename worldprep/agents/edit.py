import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import delete

from ..config import ROOT, get_settings
from ..db import session
from ..logging_setup import log
from ..models import Asset, Episode
from ..render.ffmpeg import concat, image_clip, media_duration, run_ffmpeg, silence, video_clip
from ..storage import get_storage
from .subtitles import build_srt

GAP = 0.35
SECTION_GAP = 0.9
BURN_SUBTITLES = True
RENDER_WORKERS = max(1, (os.cpu_count() or 2) // 2)
BRAND_IMAGE = ROOT / "opening" / "Brand.png"
MAX_HOOK_SPEEDUP = 1.25


def pick_music(episode_id: int, seconds: float) -> dict | None:
    lib = get_settings().music_dir / "library.json"
    if not lib.exists():
        return None
    tracks = [t for t in json.loads(lib.read_text(encoding="utf-8"))
              if t.get("license") and (get_settings().music_dir / t["file"]).exists()]
    if not tracks:
        return None
    t = tracks[episode_id % len(tracks)]
    t["path"] = str(get_settings().music_dir / t["file"])
    return t


def fit_hook(timings: dict, hook_ids: list[str], adir: Path, limit: float) -> dict:
    """開場 Hook 必須在 limit 秒內講完；超過時把 Hook 旁白整體加速（最多 1.25 倍），仍超過則由 QA 提醒。"""
    total = sum(timings[s]["duration"] for s in hook_ids) + GAP * max(0, len(hook_ids) - 1)
    if total <= limit or not hook_ids:
        return {}
    factor = min(MAX_HOOK_SPEEDUP, total / limit)
    out = {}
    for sid in hook_ids:
        wav = adir / f"{sid}_hook.wav"
        run_ffmpeg(["-i", timings[sid]["file"], "-af", f"atempo={factor:.3f}", str(wav)])
        out[sid] = {"file": str(wav), "duration": media_duration(wav)}
    log.info("hook_sped_up", extra={"factor": round(factor, 3), "seconds_before": round(total, 2)})
    return out


def opening_table(timeline: list[dict], brand_at: float | None, brand_len: float, brand_line: str) -> str:
    """開場製作表（opening/opening_prompt.txt 第 7 節），用實際剪輯時間填寫。"""
    rows = ["| 時間 | 畫面 | 旁白 |", "|---|---|---|"]
    for sc in timeline:
        if sc["section"] != "hook":
            break
        rows.append(f"| {sc['start']:05.2f}–{sc['start'] + sc['duration']:05.2f} | "
                    f"{'封面主視覺' if sc is timeline[0] else sc['scene_id']} | {sc['text']} |")
    if brand_at is not None:
        rows.append(f"| {brand_at:05.2f}–{brand_at + brand_len:05.2f} | 品牌圖 Brand.png | {brand_line} |")
        rows.append(f"| {brand_at + brand_len:05.2f} 起 | 正文 | {next((s['text'] for s in timeline if s['section'] != 'hook'), '')} |")
    return "# 開場製作表\n\n" + "\n".join(rows) + "\n"


def run(p, episode_id: int) -> None:
    st = get_storage()
    final = st.path(episode_id, "final", "episode.mp4")
    if final.exists() and st.exists(episode_id, "video", "timeline.json"):
        return
    started = time.monotonic()
    scenes = st.read_json(episode_id, "scripts", "storyboard.json")
    manifest = st.read_json(episode_id, "assets", "manifest.json")
    timings = st.read_json(episode_id, "audio", "timings.json")
    vdir = st.path(episode_id, "video", "x").parent
    adir = st.path(episode_id, "audio", "x").parent

    cfg = get_settings()
    cover = st.path(episode_id, "thumbnails", "thumbnail.jpg")
    hook_ids = [sc["scene_id"] for sc in scenes if sc["section"] == "hook"]
    hook_audio = fit_hook(timings, hook_ids, adir, cfg.hook_max_seconds)

    video_parts, audio_parts, timeline = [], [], []
    t = 0.0
    pending = []
    brand_at = None
    for i, sc in enumerate(scenes):
        sid = sc["scene_id"]
        voice = hook_audio.get(sid) or timings[sid]
        nxt = scenes[i + 1] if i + 1 < len(scenes) else None
        last_hook = sc["section"] == "hook" and (nxt is None or nxt["section"] != "hook")
        pad = cfg.brand_pause_seconds if last_hook else SECTION_GAP if nxt and nxt["section_start"] else GAP
        dur = voice["duration"] + pad
        m = manifest[sid]
        clip = vdir / f"{sid}.mp4"
        fade = 0.12 if sc.get("transition") == "cut" else 0.35
        if i == 0 and cover.exists():
            # 第一幀直接承接封面主視覺（封面可能重做，所以每次重剪）
            pending.append((image_clip, (cover, None, dur, "static", clip, 0.05)))
        elif not clip.exists() or abs(media_duration(clip) - dur) > 0.15:
            if m.get("media_type") == "video":
                pending.append((video_clip, (Path(m["file_path"]), Path(m["overlay"]), dur, clip, fade)))
            else:
                pending.append((image_clip, (Path(m["file_path"]), Path(m["overlay"]), dur, sc.get("camera_motion", "zoom_in"), clip, fade)))
        video_parts.append(clip)
        audio_parts += [Path(voice["file"]), silence(pad, adir / f"pad_{pad:.2f}.wav")]
        timeline.append({"scene_id": sid, "section": sc["section"], "heading": sc["heading"], "section_start": sc["section_start"],
                         "start": t, "duration": voice["duration"], "text": sc["script_text"]})
        t += dur
        if last_hook:
            # Hook 與空白停頓之後：固定品牌圖＋品牌台詞，接著直接進正文
            brand_at = t
            brand_wav = st.path(episode_id, "audio", "brand.wav")
            brand_len = media_duration(brand_wav) + cfg.brand_pause_seconds
            brand_clip = vdir / "brand.mp4"
            pending.append((image_clip, (BRAND_IMAGE, None, brand_len, "static", brand_clip, 0.2)))
            video_parts.append(brand_clip)
            audio_parts += [brand_wav, silence(cfg.brand_pause_seconds, adir / "pad_brand.wav")]
            t += brand_len

    with ThreadPoolExecutor(max_workers=RENDER_WORKERS) as pool:
        list(pool.map(lambda job: job[0](*job[1]), pending))

    silent_video = concat(video_parts, vdir / "video_silent.mp4")
    narration = concat(audio_parts, adir / "narration.wav")
    srt = st.write_text(episode_id, "subtitles", "zh-Hant.srt", build_srt(timeline))
    st.write_json(episode_id, "video", "timeline.json",
                  {"hook_end": brand_at or 0.0, "brand_end": (brand_at or 0.0) + (brand_len if brand_at is not None else 0.0),
                   "total": t, "scenes": timeline})
    st.write_text(episode_id, "scripts", "opening.md", opening_table(timeline, brand_at, brand_len if brand_at is not None else 0.0,
                                                                      cfg.brand_line))

    music = pick_music(episode_id, t)
    inputs = ["-i", str(silent_video), "-i", str(narration)]
    if music:
        inputs += ["-stream_loop", "-1", "-i", music["path"]]
        af = (f"[2:a]volume=0.12,afade=t=out:st={max(0, t - 3):.2f}:d=3[m];"
              f"[1:a][m]amix=inputs=2:duration=first:dropout_transition=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    else:
        af = "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    vf = "[0:v]null[v]"
    if BURN_SUBTITLES:
        shutil.copy(srt, vdir / "subs.srt")
        style = f"FontName={get_settings().subtitle_font},FontSize=15,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=1.6,Shadow=0,MarginV=36"
        vf = f"[0:v]subtitles=subs.srt:force_style='{style}'[v]"
    tmp = vdir / "episode_tmp.mp4"
    run_ffmpeg([*inputs, "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", "30",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-shortest", tmp.name], cwd=vdir)
    shutil.move(tmp, final)

    with session() as s:
        s.execute(delete(Asset).where(Asset.episode_id == episode_id, Asset.asset_type == "music"))
        if music:
            s.add(Asset(episode_id=episode_id, asset_type="music", source=music.get("source", ""), creator=music.get("artist", ""),
                        license=music["license"], license_url=music.get("license_url", ""), usage_rights=music.get("usage_rights", ""),
                        attribution_required=bool(music.get("attribution_required")), file_path=music["path"]))
        s.get(Episode, episode_id).duration_seconds = media_duration(final)
    log.info("render_done", extra={"episode_id": episode_id, "seconds": round(time.monotonic() - started, 1), "video_seconds": round(t, 1)})
