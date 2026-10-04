import re
from pathlib import Path

from .config import ROOT, get_settings

CHANNEL_NAME = "世界先修課"
CHANNEL_NAME_EN = "GLOBAL SMARTER BEFORE YOU GO"
SLOGAN = "先看懂世界，再出發。"
SECONDARY_SLOGAN = "旅行，不只是看風景。"

NAVY = (11, 27, 58)
NAVY_DEEP = (6, 15, 34)
GOLD = (212, 168, 83)
WHITE = (255, 255, 255)
SKY = (138, 176, 207)
GRAY = (128, 136, 148)

CHANNEL_DESCRIPTION = (
    "如果旅行是一堂課，這裡就是你的先修課。\n\n"
    "我們從歷史、城市、商業、文化，到最後真正值得你親眼看見的景點，"
    "帶你在出發之前，先看懂一個地方。\n\n"
    "因為旅行，不只是看風景。\n\n"
    "先看懂世界，再出發。"
)

BANNED_TITLE_PATTERNS = [r"旅遊攻略", r"十大景點", r"必去景點", r"旅遊介紹", r"景點推薦", r"完整介紹", r"Top\s*10"]
BANNED_OPENINGS = ["大家好", "歡迎來到世界先修課", "歡迎收看"]

TITLE_SUFFIX_RE = re.compile(r"｜世界先修課 EP\.(\d{2,})$")


def ep_label(n: int) -> str:
    return f"EP.{n:02d}"


def format_title(main: str, episode_number: int) -> str:
    main = TITLE_SUFFIX_RE.sub("", main.strip()).rstrip("｜| ")
    return f"{main}｜{CHANNEL_NAME} {ep_label(episode_number)}"


def validate_title(title: str, episode_number: int) -> list[str]:
    errors = []
    m = TITLE_SUFFIX_RE.search(title)
    if not m:
        errors.append("標題缺少「｜世界先修課 EP.xx」")
    elif int(m.group(1)) != episode_number:
        errors.append(f"EP 編號錯誤：{m.group(1)} != {episode_number}")
    for p in BANNED_TITLE_PATTERNS:
        if re.search(p, title, re.I):
            errors.append(f"標題含違反定位的字詞：{p}")
    if len(title) > 100:
        errors.append("標題超過 YouTube 100 字元上限")
    return errors


def _find_font(candidates: list[str]) -> str:
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise FileNotFoundError("找不到中文字型，請在 .env 設定 FONT_BOLD / FONT_REGULAR")


def font_bold() -> str:
    s = get_settings()
    return _find_font([s.font_bold, str(ROOT / "brand" / "fonts" / "NotoSansTC-Bold.ttf"),
                       "C:/Windows/Fonts/msjhbd.ttc", "C:/Windows/Fonts/NotoSansTC-VF.ttf",
                       "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/System/Library/Fonts/PingFang.ttc"])


def font_regular() -> str:
    s = get_settings()
    return _find_font([s.font_regular, str(ROOT / "brand" / "fonts" / "NotoSansTC-Regular.ttf"),
                       "C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/NotoSansTC-VF.ttf",
                       "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/System/Library/Fonts/PingFang.ttc"])


BRAND_DIR = ROOT / "brand"
