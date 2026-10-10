"""動態畫面渲染：HyperFrames（地圖動畫）與 Remotion（Shorts 跳字幕），都在 motion/ 底下的 Node 專案執行。
任何一步失敗都拋出例外，由呼叫端退回原本的靜態做法。"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx

from ..config import ROOT
from ..logging_setup import log
from .ffmpeg import ffmpeg_exe

MOTION = ROOT / "motion"
MAP_TEMPLATE = MOTION / "map" / "template.html"


def available() -> bool:
    return os.environ.get("MOTION", "on") != "off" and (MOTION / "node_modules").exists() and shutil.which("node") is not None


def _env() -> dict:
    """HyperFrames / Remotion 需要系統上找得到 ffmpeg；本機沒有時借用 imageio-ffmpeg 的執行檔。"""
    env = dict(os.environ)
    ext = ".exe" if os.name == "nt" else ""
    shim = MOTION / ".bin"
    if not shutil.which("ffmpeg"):
        shim.mkdir(exist_ok=True)
        if not (shim / f"ffmpeg{ext}").exists():
            shutil.copy(ffmpeg_exe(), shim / f"ffmpeg{ext}")
    if not shutil.which("ffprobe"):
        probe = next(MOTION.glob(f"node_modules/ffprobe-static/bin/*/*/ffprobe{ext}"), None)
        if probe:
            shim.mkdir(exist_ok=True)
            if not (shim / f"ffprobe{ext}").exists():
                shutil.copy(probe, shim / f"ffprobe{ext}")
    if shim.exists():
        env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"
    return env


def _run(args: list[str], timeout: int = 1800) -> None:
    npx = shutil.which("npx") or "npx"
    r = subprocess.run([npx, *args], cwd=MOTION, env=_env(), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{args[0]} 失敗：{(r.stderr or r.stdout)[-1500:]}")


def geocode(name: str, fallback: tuple[float, float]) -> tuple[float, float]:
    """用 OpenStreetMap（Nominatim）查真實座標；查不到或離 AI 給的座標太遠（> 300 km）就用 AI 的。"""
    try:
        r = httpx.get("https://nominatim.openstreetmap.org/search", params={"q": name, "format": "json", "limit": 1},
                      headers={"User-Agent": "worldprep/1.0 (beyondtravelwithus@gmail.com)"}, timeout=20)
        hits = r.json()
        if hits:
            lat, lon = float(hits[0]["lat"]), float(hits[0]["lon"])
            if abs(lat - fallback[0]) < 3 and abs(lon - fallback[1]) < 3:
                return lat, lon
    except Exception as e:
        log.warning("geocode_failed", extra={"name": name, "err": str(e)[:200]})
    return fallback


def render_captions(base: Path, cues: list[tuple[float, float, str]], seconds: float, out: Path, lang: str,
                    bottom: int = 400) -> Path:
    """Shorts 跳字幕（Remotion）：base 是已排好版的直式影片（含聲音），只在上面疊動態字幕。"""
    work = out.parent / f"{out.stem}_rm"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    shutil.copy(base, work / "base.mp4")
    props = {"video": "base.mp4", "duration": seconds, "bottom": bottom, "lang": lang,
             "cues": [{"start": a, "end": b, "text": t} for a, b, t in cues]}
    (work / "props.json").write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    _run(["remotion", "render", "remotion/src/index.ts", "Captions", str(out.resolve()), f"--props={(work / 'props.json').resolve()}",
          f"--public-dir={work.resolve()}", "--codec=h264", "--crf=20", "--log=error"])
    shutil.rmtree(work, ignore_errors=True)
    if not out.exists():
        raise RuntimeError("Remotion 沒有輸出影片")
    return out


def render_map(places: list[dict], route: bool, seconds: float, out: Path) -> Path:
    """places: [{label, query, lat, lon}]，輸出與場景等長、無聲的 1920×1080 影片。"""
    spec = {"route": route, "places": [{"label": p["label"], "lat": p["lat"], "lon": p["lon"]} for p in places]}
    work = out.parent / f"{out.stem}_hf"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(MOTION / "map" / "vendor", work / "vendor")
    html = (MAP_TEMPLATE.read_text(encoding="utf-8").replace("__DURATION__", f"{seconds:.3f}")
            .replace("__SPEC__", json.dumps(spec, ensure_ascii=False)))
    (work / "index.html").write_text(html, encoding="utf-8")
    _run(["hyperframes", "render", str(work), "-o", str(out.resolve()), "--fps", "30", "--quality", "standard", "--quiet"])
    shutil.rmtree(work, ignore_errors=True)
    if not out.exists():
        raise RuntimeError("HyperFrames 沒有輸出影片")
    return out
