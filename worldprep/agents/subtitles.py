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


def cues(timeline: list[dict], max_len: int = MAX_LINE) -> list[tuple[float, float, str]]:
    """timeline: [{start, duration, text}]；以字數比例分配片語時間，回傳 (開始, 結束, 字幕)。"""
    out = []
    for seg in timeline:
        ps = phrases(seg["text"], max_len)
        total = sum(len(p) for p in ps) or 1
        t = seg["start"]
        for p in ps:
            d = seg["duration"] * len(p) / total
            out.append((t, t + d - 0.05, re.sub(r"[，。、；：,;:]$", "", p)))
            t += d
    return out


def build_srt(timeline: list[dict]) -> str:
    return "\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{text}\n" for i, (a, b, text) in enumerate(cues(timeline), 1))
