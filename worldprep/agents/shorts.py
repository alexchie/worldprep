"""短影音：從成品剪出開場 Hook＋品牌台詞，轉成直式（模糊背景），結尾接 5 秒導流定格。"""
from pathlib import Path

from ..logging_setup import log
from ..render import cards
from ..render.ffmpeg import FPS, media_duration, run_ffmpeg
from ..storage import get_storage

ENDCARD_SECONDS = 5.0
W, H = cards.SHORTS_W, cards.SHORTS_H


def run(p, episode_id: int, force: bool = False) -> Path | None:
    st = get_storage()
    out = st.path(episode_id, "final", "short.mp4")
    if out.exists() and not force:
        return out
    final = st.path(episode_id, "final", "episode.mp4")
    end = st.read_json(episode_id, "video", "timeline.json").get("brand_end", 0.0)
    if not final.exists() or end <= 0:
        log.warning("short_skipped", extra={"episode_id": episode_id, "brand_end": end})
        return None
    card = cards.shorts_endcard(st.path(episode_id, "thumbnails", "thumbnail.jpg"),
                                st.path(episode_id, "thumbnails", "short_endcard.jpg"))
    fade = 0.4
    graph = (
        f"[0:v]trim=0:{end:.3f},setpts=PTS-STARTPTS,split[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=40:2,eq=brightness=-0.12[bg];"
        f"[b]scale={W}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS},setsar=1,format=yuv420p[v0];"
        f"[1:v]scale={W}:{H},fps={FPS},setsar=1,fade=t=in:st=0:d={fade},format=yuv420p[v1];"
        f"[0:a]atrim=0:{end:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0, end - fade):.3f}:d={fade}[a0];"
        f"[2:a]atrim=0:{ENDCARD_SECONDS}[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
    )
    run_ffmpeg(["-i", str(final), "-loop", "1", "-t", str(ENDCARD_SECONDS), "-i", str(card),
                "-f", "lavfi", "-t", str(ENDCARD_SECONDS), "-i", "anullsrc=r=48000:cl=stereo",
                "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(out)])
    log.info("short_done", extra={"episode_id": episode_id, "seconds": round(media_duration(out), 1)})
    return out
