import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..brand import BRAND_DIR, CHANNEL_NAME, CHANNEL_NAME_EN, GOLD, NAVY, NAVY_DEEP, WHITE, font_bold, font_regular

SS = 4

PLANE = [(1.0, 0.0), (0.55, 0.08), (0.2, 0.55), (0.05, 0.55), (0.22, 0.08), (-0.35, 0.08), (-0.5, 0.3),
         (-0.6, 0.3), (-0.5, 0.0), (-0.6, -0.3), (-0.5, -0.3), (-0.35, -0.08), (0.22, -0.08), (0.05, -0.55),
         (0.2, -0.55), (0.55, -0.08)]


def _plane(d: ImageDraw.ImageDraw, cx: float, cy: float, size: float, angle: float, fill) -> None:
    a = math.radians(angle)
    pts = [(cx + size * (x * math.cos(a) - y * math.sin(a)), cy + size * (x * math.sin(a) + y * math.cos(a))) for x, y in PLANE]
    d.polygon(pts, fill=fill)


def icon(size: int = 1024, bg=NAVY, globe=WHITE, accent=GOLD, transparent: bool = False) -> Image.Image:
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if not transparent:
        d.ellipse([0, 0, S - 1, S - 1], fill=bg)
    c, r = S / 2, S * 0.30
    w = max(2, int(S * 0.018))
    box = [c - r, c - r, c + r, c + r]
    d.ellipse(box, outline=globe, width=w)
    for k in (0.35, 0.72):
        d.ellipse([c - r * k, c - r, c + r * k, c + r], outline=globe, width=w)
    d.line([c, c - r, c, c + r], fill=globe, width=w)
    for lat in (-0.5, 0, 0.5):
        y = c + r * lat
        half = r * math.sqrt(1 - lat * lat) - w
        d.line([c - half, y, c + half, y], fill=globe, width=w)
    R = S * 0.41
    arc_box = [c - R, c - R * 0.78, c + R, c + R * 0.78]
    d.arc(arc_box, start=150, end=338, fill=accent, width=int(w * 1.3))
    end = math.radians(338)
    px, py = c + R * math.cos(end), c + R * 0.78 * math.sin(end)
    heading = math.degrees(math.atan2(R * 0.78 * math.cos(end), -R * math.sin(end)))
    _plane(d, px, py, S * 0.11, heading, accent)
    return img.resize((size, size), Image.LANCZOS)


def horizontal(height: int = 400, dark: bool = True, transparent: bool = False) -> Image.Image:
    bg = NAVY_DEEP if dark else (247, 244, 236)
    fg = WHITE if dark else NAVY
    width = int(height * 3.6)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0) if transparent else bg + (255,))
    ic = icon(int(height * 0.86), bg=NAVY, globe=WHITE, accent=GOLD)
    img.alpha_composite(ic, (int(height * 0.07), int(height * 0.07)))
    d = ImageDraw.Draw(img)
    tx = int(height * 1.05)
    d.text((tx, int(height * 0.14)), CHANNEL_NAME, font=ImageFont.truetype(font_bold(), int(height * 0.40)), fill=fg)
    d.text((tx + 4, int(height * 0.66)), CHANNEL_NAME_EN, font=ImageFont.truetype(font_regular(), int(height * 0.11)), fill=GOLD)
    return img


def monochrome(img: Image.Image, color=WHITE) -> Image.Image:
    alpha = img.getchannel("A")
    out = Image.new("RGBA", img.size, color + (0,))
    out.putalpha(alpha)
    return out


def generate_all(out_dir: Path = BRAND_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "logo_icon_1x1_dark.png": icon(1024),
        "logo_icon_1x1_light.png": icon(1024, bg=(247, 244, 236), globe=NAVY, accent=GOLD),
        "logo_icon_transparent.png": icon(1024, transparent=True),
        "logo_icon_mono_white.png": monochrome(icon(1024, transparent=True), WHITE),
        "logo_icon_mono_navy.png": monochrome(icon(1024, transparent=True), NAVY),
        "logo_horizontal_dark.png": horizontal(400, dark=True),
        "logo_horizontal_light.png": horizontal(400, dark=False),
        "logo_horizontal_transparent.png": horizontal(400, dark=True, transparent=True),
        "youtube_profile_800.png": icon(800),
        "watermark_150.png": icon(150),
    }
    paths = []
    for name, im in files.items():
        p = out_dir / name
        im.save(p)
        paths.append(p)
    ico = out_dir / "favicon.ico"
    icon(256).save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    paths.append(ico)
    return paths


def ensure_logo() -> Path:
    p = BRAND_DIR / "logo_icon_1x1_dark.png"
    if not p.exists():
        generate_all()
    return p


def mark(height: int) -> Image.Image:
    """頻道 Logo 圖案（地球＋飛機，透明底，取自 Brand.png），依高度等比縮放。"""
    img = Image.open(BRAND_DIR / "logo_mark.png").convert("RGBA")
    return img.resize((round(img.width * height / img.height), height), Image.LANCZOS)
