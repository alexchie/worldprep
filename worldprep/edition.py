"""語言版本：中文版（世界先修課）與英文版（Beyond Travel）共用同一條剪輯流程，差在品牌文字、配音、字幕與工作目錄。"""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .brand import CHANNEL_NAME, SLOGAN
from .config import ROOT, get_settings


@dataclass(frozen=True)
class Edition:
    lang: str
    channel: str
    brand_lines: tuple[str, ...]  # 封面左上角的品牌文字（由上而下）
    tags: tuple[str, ...]  # 封面底部五大標籤
    brand_line: str  # 開場品牌台詞
    brand_image: Path
    voice: str
    endcard_lines: tuple[str, ...]  # 短影音結尾導流文字
    sub_max: int  # 正片字幕一行上限（中文為字數、英文為字元數）
    short_sub_max: int


ZH = Edition(
    lang="zh", channel=CHANNEL_NAME, brand_lines=(CHANNEL_NAME, "Beyond Travel", SLOGAN),
    tags=("歷史", "城市", "商業", "文化", "景點"), brand_line="世界先修課，跟著我們一起看懂世界再出發",
    brand_image=ROOT / "opening" / "Brand.png", voice="", endcard_lines=("詳細說明", "請點以下Youtube連結"),
    sub_max=18, short_sub_max=12,
)
EN = Edition(
    lang="en", channel="Beyond Travel", brand_lines=("Beyond Travel", "Understand the world before you go."),
    tags=("History", "Cities", "Business", "Culture", "Sights"), brand_line="This is Beyond Travel. Understand the world before you go.",
    brand_image=ROOT / "opening" / "Brand_en.png", voice="en-US-AvaNeural", endcard_lines=("Full story", "Watch the video below"),
    sub_max=42, short_sub_max=24,
)

_current = ZH


def current() -> Edition:
    return _current


@contextmanager
def use(ed: Edition, episode_id: int):
    """切換到指定語言版本；英文版的工作檔放在該集工作目錄下的 en/，跟著中文版一起同步到雲端。"""
    from . import storage

    global _current
    prev_ed, prev_st = _current, storage._storage
    _current = ed
    if ed.lang != "zh":
        storage._storage = storage.LocalStorage(get_settings().storage_root / f"ep{episode_id:04d}" / ed.lang)
    try:
        yield
    finally:
        _current, storage._storage = prev_ed, prev_st
