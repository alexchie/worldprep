import json
from pathlib import Path

from ..config import ROOT
from ..logging_setup import log
from ..storage import get_storage


def run(p, episode_id: int) -> None:
    st = get_storage()
    scenes = st.read_json(episode_id, "scripts", "storyboard.json")
    name = "timings.json"
    timings = st.read_json(episode_id, "audio", name) if st.exists(episode_id, "audio", name) else {}
    for sc in scenes:
        sid = sc["scene_id"]
        t = timings.get(sid)
        if t and Path(t["file"]).exists() and t["text"] == sc["script_text"]:
            continue
        r = p.voice.synthesize(sc["script_text"], st.path(episode_id, "audio", sid), episode_id)
        timings[sid] = {"file": str(r.path), "duration": r.duration, "chars": r.chars, "text": sc["script_text"]}
        st.write_json(episode_id, "audio", name, timings)
    if p.voice.name != "mock":
        calibrate(timings)


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
