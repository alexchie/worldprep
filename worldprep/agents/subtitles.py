import math
import re

MAX_LINE = 18


def phrases(text: str, max_len: int = MAX_LINE) -> list[str]:
    parts = [x for x in re.split(r"(?<=[，。！？；：、,!?;:])", text) if x.strip()]
    out = []
    for part in parts:
        part = part.strip()
        if len(part) > max_len:
            n = math.ceil(len(part) / max_len)
            size = math.ceil(len(part) / n)
            out += [part[i:i + size] for i in range(0, len(part) - size, size)]
            part = part[size * (n - 1):]
        if part:
            if out and len(out[-1]) + len(part) <= max_len * 0.6:
                out[-1] += part
            else:
                out.append(part)
    return out


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(timeline: list[dict]) -> str:
    """timeline: [{start, duration, text}]；以字數比例分配片語時間。"""
    cues, idx = [], 1
    for seg in timeline:
        ps = phrases(seg["text"])
        total = sum(len(p) for p in ps) or 1
        t = seg["start"]
        for p in ps:
            d = seg["duration"] * len(p) / total
            clean = re.sub(r"[，。、；：,;:]$", "", p)
            cues.append(f"{idx}\n{_ts(t)} --> {_ts(t + d - 0.05)}\n{clean}\n")
            idx += 1
            t += d
    return "\n".join(cues)
