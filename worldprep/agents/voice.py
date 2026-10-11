import json
from pathlib import Path

from .. import edition
from ..config import ROOT
from ..providers.voice import EdgeVoice
from ..logging_setup import log
from ..storage import get_storage


def run(p, episode_id: int) -> None:
    st = get_storage()
    ed = edition.current()
    # 英文版換成英文配音員（mock 模式沿用 mock）
    tts = EdgeVoice(ed.voice) if ed.voice and p.voice.name != "mock" else p.voice
    scenes = st.read_json(episode_id, "scripts", "storyboard.json")
    name = "timings.json"
    timings = st.read_json(episode_id, "audio", name) if st.exists(episode_id, "audio", name) else {}
    for sc in scenes:
        sid = sc["scene_id"]
        t = timings.get(sid)
        if t and Path(t["file"]).exists() and t["text"] == sc["script_text"]:
            continue
        r = tts.synthesize(sc["script_text"], st.path(episode_id, "audio", sid), episode_id, rate=pace(sc["script_text"]))
        timings[sid] = {"file": str(r.path), "duration": r.duration, "chars": r.chars, "text": sc["script_text"]}
        st.write_json(episode_id, "audio", name, timings)
    if not st.exists(episode_id, "audio", "brand.wav"):
        # 固定品牌台詞（Hook 之後），與正文同語速
        tts.synthesize(ed.brand_line, st.path(episode_id, "audio", "brand"), episode_id)
    if tts.name != "mock" and ed.lang == "zh":  # 語速校正只用中文
        calibrate(timings)


def pace(text: str) -> str:
    """免費版的語氣變化：問句與短促的關鍵句放慢一點，其餘維持原本語速。"""
    t = text.strip()
    if t.endswith(("？", "?")) or len(t) <= 12:
        return "-9%"
    return "-3%"


def calibrate(timings: dict) -> None:
    chars = sum(t["chars"] for t in timings.values())
    minutes = sum(t["duration"] for t in timings.values()) / 60
    if minutes <= 0:
        return
    measured = chars / minutes
    cal = ROOT / "data" / "calibration.json"
    old = json.loads(cal.read_text(encoding="utf-8"))["chars_per_minute"] if cal.exists() else measured
    new = round(0.6 * measured + 0.4 * old)
    cal.parent.mkdir(parents=True, exist_ok=True)
    cal.write_text(json.dumps({"chars_per_minute": new}), encoding="utf-8")
    log.info("narration_calibrated", extra={"measured_cpm": round(measured), "stored_cpm": new})
