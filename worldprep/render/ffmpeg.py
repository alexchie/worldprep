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


def image_clip(image: Path, overlay: Path | None, seconds: float, motion: str, out: Path, fade: float = 0.35,
               fade_in: float | None = None, fade_out: float | None = None) -> Path:
    n = max(1, int(round(seconds * FPS)))
    fi, fo = fade if fade_in is None else fade_in, fade if fade_out is None else fade_out
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
    fades = ([f"fade=t=in:st=0:d={fi}"] if fi > 0 else []) + ([f"fade=t=out:st={max(0, seconds - fo):.3f}:d={fo}"] if fo > 0 else [])
    vf += f";[v0]{','.join([*fades, 'format=yuv420p'])}[v]"
    args += ["-filter_complex", vf, "-map", "[v]", "-frames:v", str(n), "-r", str(FPS),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(out)]
    run_ffmpeg(args)
    return out


# 特寫取景：畫面中最有看頭的區域（由投影片審核時標出），放大 1.35 倍
FOCUS = {"center": (0.5, 0.5), "left": (0.0, 0.5), "right": (1.0, 0.5), "top": (0.5, 0.0), "bottom": (0.5, 1.0),
         "top_left": (0.0, 0.0), "top_right": (1.0, 0.0), "bottom_left": (0.0, 1.0), "bottom_right": (1.0, 1.0)}
CLOSEUP_ZOOM = 1.35
SPLIT_MIN_SECONDS = 4.5


def closeup_image(image: Path, focus: str, out: Path) -> Path:
    from PIL import Image

    im = Image.open(image).convert("RGB")
    s = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    cw, ch = round(W / CLOSEUP_ZOOM), round(H / CLOSEUP_ZOOM)
    fx, fy = FOCUS.get(focus, FOCUS["center"])
    left, top = round((im.width - cw) * fx), round((im.height - ch) * fy)
    im.crop((left, top, left + cw, top + ch)).resize((W, H), Image.LANCZOS).save(out, quality=95)
    return out


def split_clip(image: Path, overlay: Path | None, seconds: float, focus: str, out: Path, fade: float = 0.35) -> Path:
    """同一張圖切成兩個鏡頭：先全景、再硬切到局部特寫。畫面完全不晃動，只靠剪接製造節奏。"""
    if seconds < SPLIT_MIN_SECONDS:
        return image_clip(image, overlay, seconds, "static", out, fade)
    n = max(2, int(round(seconds * FPS)))
    n1 = int(round(n * 0.55))
    wide, close = out.with_name(f"{out.stem}_a.mp4"), out.with_name(f"{out.stem}_b.mp4")
    zoomed = closeup_image(image, focus, out.with_name(f"{out.stem}_close.jpg"))
    image_clip(image, overlay, n1 / FPS, "static", wide, fade, fade_out=0)
    image_clip(zoomed, overlay, (n - n1) / FPS, "static", close, fade, fade_in=0)
    concat([wide, close], out)
    for f in (wide, close, zoomed, out.with_suffix(".txt")):
        f.unlink(missing_ok=True)
    return out


def outline_clip(base: Path, item: Path | None, overlay: Path | None, seconds: float, out: Path,
                 fade_in: float = 0.35, fade_out: float = 0.35, slide: float = 0.45) -> Path:
    """大綱動畫：底圖是已出現的重點，新的一條在開頭從左側滑入並淡入（緩出曲線）。"""
    n = max(1, int(round(seconds * FPS)))
    args = ["-loop", "1", "-i", str(base)]
    vf = f"[0:v]scale={W}:{H},setsar=1[b0]"
    cur, idx = "b0", 1
    if item:
        args += ["-loop", "1", "-i", str(item)]
        vf += (f";[1:v]format=rgba,fade=t=in:st=0:d={slide}:alpha=1[it]"
               f";[b0][it]overlay=x='-120*pow(1-min(1,t/{slide}),2)':y=0[b1]")
        cur, idx = "b1", 2
    if overlay:
        args += ["-loop", "1", "-i", str(overlay)]
        vf += f";[{cur}][{idx}:v]overlay=0:0[b2]"
        cur = "b2"
    fades = ([f"fade=t=in:st=0:d={fade_in}"] if fade_in > 0 else []) + (
        [f"fade=t=out:st={max(0, seconds - fade_out):.3f}:d={fade_out}"] if fade_out > 0 else [])
    vf += f";[{cur}]{','.join([*fades, 'format=yuv420p'])}[v]"
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
