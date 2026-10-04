import re
import subprocess
import time
from pathlib import Path

from ..config import get_settings
from ..logging_setup import log

W, H, FPS = 1920, 1080, 30


def ffmpeg_exe() -> str:
    p = get_settings().ffmpeg_path
    if p:
        return p
    import shutil

    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str], cwd: Path | None = None, timeout: int = 3600) -> str:
    cmd = [ffmpeg_exe(), "-hide_banner", "-y", *args]
    started = time.monotonic()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({p.returncode}): {p.stderr[-1500:]}")
    elapsed = time.monotonic() - started
    if elapsed > 20:
        log.info("ffmpeg", extra={"seconds": round(elapsed, 1), "out": args[-1]})
    return p.stderr


def probe(path: Path) -> dict:
    """以 ffmpeg -i 解析時長/解析度/幀率/音訊（imageio-ffmpeg 不附 ffprobe）。"""
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    err = p.stderr
    info: dict = {"duration": 0.0, "width": 0, "height": 0, "fps": 0.0, "has_audio": "Audio:" in err, "has_video": "Video:" in err}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Video:.*?, (\d{2,5})x(\d{2,5})", err)
    if m:
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"([\d.]+) fps", err)
    if m:
        info["fps"] = float(m.group(1))
    return info


def media_duration(path: Path) -> float:
    return probe(path)["duration"]


def volume_stats(path: Path) -> dict:
    err = run_ffmpeg(["-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"])
    mean = re.search(r"mean_volume: ([-\d.]+) dB", err)
    peak = re.search(r"max_volume: ([-\d.]+) dB", err)
    return {"mean_db": float(mean.group(1)) if mean else None, "max_db": float(peak.group(1)) if peak else None}


MOTIONS = {
    "zoom_in": ("min(zoom+0.0006,1.18)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "zoom_out": ("if(eq(on,0),1.18,max(zoom-0.0006,1.0))", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "pan_left": ("1.15", "(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"),
    "pan_right": ("1.15", "(iw-iw/zoom)*on/{n}", "ih/2-(ih/zoom/2)"),
    "static": ("1.0", "0", "0"),
}


def image_clip(image: Path, overlay: Path | None, seconds: float, motion: str, out: Path, fade: float = 0.35) -> Path:
    n = max(1, int(round(seconds * FPS)))
    z, x, y = MOTIONS.get(motion, MOTIONS["zoom_in"])
    x, y = x.format(n=n), y.format(n=n)
    vf = (f"[0:v]scale={W * 3 // 2}:{H * 3 // 2}:force_original_aspect_ratio=increase,crop={W * 3 // 2}:{H * 3 // 2},"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},setsar=1[bg]")
    args = ["-loop", "1", "-i", str(image)]
    if overlay:
        args += ["-loop", "1", "-i", str(overlay)]
        vf += ";[bg][1:v]overlay=0:0:shortest=1[v0]"
    else:
        vf += ";[bg]null[v0]"
    vf += f";[v0]fade=t=in:st=0:d={fade},fade=t=out:st={max(0, seconds - fade):.3f}:d={fade},format=yuv420p[v]"
    args += ["-filter_complex", vf, "-map", "[v]", "-frames:v", str(n), "-r", str(FPS),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(out)]
    run_ffmpeg(args)
    return out


def video_clip(video: Path, overlay: Path | None, seconds: float, out: Path, fade: float = 0.35) -> Path:
    """實拍片段：裁成 1080p、統一 30fps、長度不足時循環、去掉原音（旁白另外混）。"""
    n = max(1, int(round(seconds * FPS)))
    vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1[bg]")
    args = ["-stream_loop", "-1", "-i", str(video)]
    if overlay:
        args += ["-loop", "1", "-i", str(overlay)]
        vf += ";[bg][1:v]overlay=0:0:shortest=1[v0]"
    else:
        vf += ";[bg]null[v0]"
    vf += f";[v0]fade=t=in:st=0:d={fade},fade=t=out:st={max(0, seconds - fade):.3f}:d={fade},format=yuv420p[v]"
    args += ["-filter_complex", vf, "-map", "[v]", "-frames:v", str(n), "-r", str(FPS),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(out)]
    run_ffmpeg(args)
    return out


def poster_frame(video: Path, out: Path, at: float = 1.0) -> Path:
    run_ffmpeg(["-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)])
    return out


def concat(files: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{f.resolve().as_posix()}'\n" for f in files), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out


def silence(seconds: float, out: Path) -> Path:
    run_ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono", "-t", f"{seconds:.3f}", str(out)])
    return out
