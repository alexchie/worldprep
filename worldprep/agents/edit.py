import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import delete

from ..config import get_settings
from ..db import session
from ..logging_setup import log
from ..models import Asset, Episode
from ..render import cards
from ..render.ffmpeg import concat, image_clip, media_duration, run_ffmpeg, silence, video_clip
from ..storage import get_storage
from .subtitles import build_srt

GAP = 0.4
SECTION_GAP = 0.8
BURN_SUBTITLES = True
RENDER_WORKERS = max(1, (os.cpu_count() or 2) // 2)


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

    hero = next((Path(m.get("poster", m["file_path"])) for m in manifest.values()
                 if m["source"].startswith("http") and not m["ai_generated"]), None)
    video_parts, audio_parts, timeline = [], [], []
    t = 0.0
    for i, (img, secs) in enumerate(cards.opening_frames(vdir / "opening", hero)):
        clip = vdir / f"open_{i}.mp4"
        if not clip.exists():
            image_clip(img, None, secs, "zoom_in" if i == 0 else "static", clip, fade=0.25)
        video_parts.append(clip)
        t += secs
    opening_len = t
    audio_parts.append(silence(opening_len, adir / "opening_silence.wav"))

    gap_wav = silence(GAP, adir / "gap.wav")
    sec_gap_wav = silence(SECTION_GAP, adir / "section_gap.wav")
    pending = []
    for i, sc in enumerate(scenes):
        sid = sc["scene_id"]
        voice = timings[sid]
        nxt_section = i + 1 < len(scenes) and scenes[i + 1]["section_start"]
        pad = SECTION_GAP if nxt_section else GAP
        dur = voice["duration"] + pad
        m = manifest[sid]
        clip = vdir / f"{sid}.mp4"
        if not clip.exists() or abs(media_duration(clip) - dur) > 0.15:
            fade = 0.12 if sc.get("transition") == "cut" else 0.35
            if m.get("media_type") == "video":
                pending.append((video_clip, (Path(m["file_path"]), Path(m["overlay"]), dur, clip, fade)))
            else:
                pending.append((image_clip, (Path(m["file_path"]), Path(m["overlay"]), dur, sc.get("camera_motion", "zoom_in"), clip, fade)))
        video_parts.append(clip)
        audio_parts += [Path(voice["file"]), sec_gap_wav if nxt_section else gap_wav]
        timeline.append({"scene_id": sid, "section": sc["section"], "heading": sc["heading"], "section_start": sc["section_start"],
                         "start": t, "duration": voice["duration"], "text": sc["script_text"]})
        t += dur

    with ThreadPoolExecutor(max_workers=RENDER_WORKERS) as pool:
        list(pool.map(lambda job: job[0](*job[1]), pending))

    silent_video = concat(video_parts, vdir / "video_silent.mp4")
    narration = concat(audio_parts, adir / "narration.wav")
    srt = st.write_text(episode_id, "subtitles", "zh-Hant.srt", build_srt(timeline))
    st.write_json(episode_id, "video", "timeline.json", {"opening": opening_len, "total": t, "scenes": timeline})

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
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-shortest", tmp.name], cwd=vdir)
    shutil.move(tmp, final)

    with session() as s:
        s.execute(delete(Asset).where(Asset.episode_id == episode_id, Asset.asset_type == "music"))
        if music:
            s.add(Asset(episode_id=episode_id, asset_type="music", source=music.get("source", ""), creator=music.get("artist", ""),
                        license=music["license"], license_url=music.get("license_url", ""), usage_rights=music.get("usage_rights", ""),
                        attribution_required=bool(music.get("attribution_required")), file_path=music["path"]))
        s.get(Episode, episode_id).duration_seconds = media_duration(final)
    log.info("render_done", extra={"episode_id": episode_id, "seconds": round(time.monotonic() - started, 1), "video_seconds": round(t, 1)})
