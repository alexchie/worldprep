"""短影音：從成品剪出開場 Hook＋品牌台詞，轉成直式（模糊背景），字幕加大放在下方模糊區，結尾接 5 秒導流定格。"""
from pathlib import Path

from ..config import get_settings
from ..logging_setup import log
from ..render import cards
from ..render.ffmpeg import FPS, media_duration, run_ffmpeg
from ..storage import get_storage
from .subtitles import cues

ENDCARD_SECONDS = 5.0
W, H = cards.SHORTS_W, cards.SHORTS_H
SUB_FONT_SIZE = 76
SUB_MAX_CHARS = 12  # 直式畫面一行約可放 12 個大字
SUB_MARGIN_BOTTOM = 400  # 落在橫式畫面下方的模糊區
# 橫式畫面置中、上下用同一畫面的模糊放大版填滿
VERTICAL = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=40:2,eq=brightness=-0.12[bg];"
            f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(timeline: list[dict], end: float) -> str:
    """直式字幕（像素座標）：只取短影音範圍內的旁白。"""
    font = get_settings().subtitle_font
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV",
        f"Style: Short,{font},{SUB_FONT_SIZE},&H00FFFFFF,&H00221006,&H80000000,1,1,5,0,2,60,60,{SUB_MARGIN_BOTTOM}",
        "", "[Events]", "Format: Layer, Start, End, Style, Text",
    ]
    for a, b, text in cues([s for s in timeline if s["start"] < end], SUB_MAX_CHARS):
        lines.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(min(b, end))},Short,{text}")
    return "\n".join(lines) + "\n"


def run(p, episode_id: int, force: bool = False) -> Path | None:
    st = get_storage()
    out = st.path(episode_id, "final", "short.mp4")
    if out.exists() and not force:
        return out
    final = st.path(episode_id, "final", "episode.mp4")
    silent = st.path(episode_id, "video", "video_silent.mp4")  # 尚未燒字幕的畫面
    timeline = st.read_json(episode_id, "video", "timeline.json")
    end = timeline.get("brand_end", 0.0)
    if not final.exists() or not silent.exists() or end <= 0:
        log.warning("short_skipped", extra={"episode_id": episode_id, "brand_end": end})
        return None
    card = cards.shorts_endcard(st.path(episode_id, "thumbnails", "thumbnail.jpg"),
                                st.path(episode_id, "thumbnails", "short_endcard.jpg"))
    ass = st.write_text(episode_id, "final", "short.ass", build_ass(timeline["scenes"], end))
    fade = 0.4
    graph = (
        f"[0:v]trim=0:{end:.3f},setpts=PTS-STARTPTS,{VERTICAL}[vv];"
        f"[vv]ass={ass.name},fps={FPS},setsar=1,format=yuv420p[v0];"
        f"[1:v]scale={W}:{H},fps={FPS},setsar=1,fade=t=in:st=0:d={fade},format=yuv420p[v1];"
        f"[2:a]atrim=0:{end:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0, end - fade):.3f}:d={fade}[a0];"
        f"[3:a]atrim=0:{ENDCARD_SECONDS}[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
    )
    # 聲音取自成品（含配樂與音量標準化），畫面取自未燒字幕的版本
    run_ffmpeg(["-i", str(silent.resolve()), "-loop", "1", "-t", str(ENDCARD_SECONDS), "-i", str(card.resolve()),
                "-i", str(final.resolve()), "-f", "lavfi", "-t", str(ENDCARD_SECONDS), "-i", "anullsrc=r=48000:cl=stereo",
                "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", out.name], cwd=out.parent)
    # Shorts 封面＝短影音的第一個畫面但不含字幕（第一幀有 0.05 秒淡入，取 0.1 秒避開黑畫面）
    run_ffmpeg(["-ss", "0.1", "-i", str(silent.resolve()), "-frames:v", "1", "-filter_complex", f"[0:v]{VERTICAL}",
                "-q:v", "2", str(st.path(episode_id, "thumbnails", "short_cover.jpg").resolve())])
    log.info("short_done", extra={"episode_id": episode_id, "seconds": round(media_duration(out), 1)})
    return out
