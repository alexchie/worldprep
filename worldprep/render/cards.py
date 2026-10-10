"""品牌化的圖卡：標題卡、地圖卡、數據圖、字卡 overlay、片頭、縮圖。"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from ..brand import CHANNEL_NAME, GOLD, GRAY, NAVY, NAVY_DEEP, SKY, WHITE, ep_label, font_bold, font_regular
from .. import edition
from .logo import icon

W, H = 1920, 1080


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_bold() if bold else font_regular(), size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if font.getlength(cur + ch) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _gradient(size=(W, H), top=NAVY, bottom=NAVY_DEEP) -> Image.Image:
    w, h = size
    g = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        g.putpixel((0, y), tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    return g.resize(size)


def title_card(text: str, sub: str, out: Path) -> Path:
    img = _gradient()
    d = ImageDraw.Draw(img)
    d.line([(160, 470), (300, 470)], fill=GOLD, width=6)
    f = _font(96)
    y = 500
    for line in _wrap(text, f, W - 320):
        d.text((160, y), line, font=f, fill=WHITE)
        y += 120
    if sub:
        d.text((160, y + 20), sub, font=_font(44, False), fill=SKY)
    img.save(out, quality=95)
    return out


def map_card(place: str, caption: str, out: Path, background: Path | None = None) -> Path:
    """地點卡：以目的地影像為底、品牌色定位標記。真實地理底圖列於 Phase 6。"""
    if background and background.exists():
        img = Image.open(background).convert("RGB")
        img = _cover(img, (W, H)).filter(ImageFilter.GaussianBlur(6))
        img = ImageEnhance.Brightness(img).enhance(0.45)
    else:
        img = _gradient()
    d = ImageDraw.Draw(img)
    for i in range(0, W, 120):
        d.line([(i, 0), (i, H)], fill=(40, 60, 95), width=1)
    for j in range(0, H, 120):
        d.line([(0, j), (W, j)], fill=(40, 60, 95), width=1)
    cx, cy = W // 2, H // 2 - 40
    for r, a in ((90, 60), (55, 120)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD if a > 100 else SKY, width=4)
    d.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill=GOLD)
    f = _font(84)
    tw = f.getlength(place)
    d.text((cx - tw / 2, cy + 120), place, font=f, fill=WHITE)
    if caption:
        fc = _font(40, False)
        for i, line in enumerate(_wrap(caption, fc, 1400)[:2]):
            d.text((cx - fc.getlength(line) / 2, cy + 240 + i * 56), line, font=fc, fill=SKY)
    img.save(out, quality=95)
    return out


def chart(data: dict, out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    fp = font_manager.FontProperties(fname=font_regular())
    fb = font_manager.FontProperties(fname=font_bold())
    hexc = lambda c: "#%02x%02x%02x" % c  # noqa: E731
    fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
    fig.patch.set_facecolor(hexc(NAVY_DEEP))
    ax.set_facecolor(hexc(NAVY_DEEP))
    labels, values = data.get("labels", []), data.get("values", [])
    kind = data.get("kind", "bar")
    if kind == "line":
        ax.plot(labels, values, color=hexc(GOLD), linewidth=5, marker="o", markersize=10)
    else:
        bars = ax.bar(labels, values, color=[hexc(SKY)] * (len(values) - 1) + [hexc(GOLD)], width=0.6)
        for b, v in zip(bars, values):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{v:,}", ha="center", va="bottom",
                    color="white", fontproperties=fp, fontsize=24)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(hexc(GRAY))
    ax.tick_params(colors="white", labelsize=22)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_fontproperties(fp)
        lbl.set_fontsize(22)
    ax.set_title(data.get("title", ""), color="white", fontproperties=fb, fontsize=44, pad=40, loc="left")
    if data.get("unit"):
        ax.set_ylabel(data["unit"], color=hexc(SKY), fontproperties=fp, fontsize=24)
    if data.get("source"):
        label = "Source: " if edition.current().lang == "en" else "資料來源："
        fig.text(0.06, 0.03, f"{label}{data['source']}", color=hexc(GRAY), fontproperties=fp, fontsize=18)
    fig.subplots_adjust(left=0.08, right=0.95, top=0.82, bottom=0.12)
    fig.savefig(out, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out


def text_overlay(text: str, out: Path, watermark: bool = True) -> Path:
    """透明疊加層：左下字卡 + 右上浮水印。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if text:
        f = _font(54)
        lines = _wrap(text, f, 1100)[:2]
        box_h = 60 + 72 * len(lines)
        y0 = H - 140 - box_h
        width = int(max(f.getlength(line) for line in lines)) + 110
        d.rectangle([90, y0, 90 + width, y0 + box_h], fill=NAVY_DEEP + (205,))
        d.rectangle([90, y0, 100, y0 + box_h], fill=GOLD + (255,))
        for i, line in enumerate(lines):
            d.text((140, y0 + 30 + i * 72), line, font=f, fill=WHITE)
    if watermark:
        wm = icon(96)
        wm.putalpha(wm.getchannel("A").point(lambda a: int(a * 0.55)))
        img.alpha_composite(wm, (W - 140, 44))
    img.save(out)
    return out


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    w, h = size
    s = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * s) + 1, int(img.height * s) + 1), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def _fit_phrase(phrase: str, max_w: int) -> tuple[list[str], int, ImageFont.FreeTypeFont]:
    """單行放得下就單行；否則平均切成兩行，避免孤字。"""
    for size in range(150, 79, -6):
        f = _font(size)
        if f.getlength(phrase) <= max_w:
            return [phrase], size, f
    half = (len(phrase) + 1) // 2
    lines = [phrase[:half], phrase[half:]]
    for size in range(140, 59, -6):
        f = _font(size)
        if max(f.getlength(x) for x in lines) <= max_w:
            return lines, size, f
    return lines, 60, _font(60)


def thumbnail(background: Path | None, phrase: str, sub: str, episode_number: int, out: Path) -> Path:
    TW, TH = 1280, 720
    if background and background.exists():
        img = _cover(Image.open(background).convert("RGB"), (TW, TH))
        img = ImageEnhance.Contrast(img).enhance(1.15)
    else:
        img = _gradient((TW, TH))
    shade = Image.new("L", (TW, 1))
    for x in range(TW):
        shade.putpixel((x, 0), int(max(0, 235 - x * 0.32)))
    shade = shade.resize((TW, TH))
    navy = Image.new("RGB", (TW, TH), NAVY_DEEP)
    img = Image.composite(navy, img, shade).convert("RGBA")
    d = ImageDraw.Draw(img)
    lines, size, f = _fit_phrase(phrase, 760)
    y = TH / 2 - len(lines) * size * 0.62
    for line in lines:
        d.text((56 + 4, y + 4), line, font=f, fill=(0, 0, 0, 160))
        d.text((56, y), line, font=f, fill=WHITE)
        y += size * 1.2
    if sub:
        fs = _font(44)
        d.rectangle([56, y + 14, 56 + fs.getlength(sub) + 36, y + 14 + 68], fill=GOLD)
        d.text((74, y + 20), sub, font=fs, fill=NAVY_DEEP)
    badge = f"{CHANNEL_NAME} | {ep_label(episode_number)}"
    fb = _font(30)
    bw = fb.getlength(badge) + 84
    d.rounded_rectangle([TW - bw - 28, 26, TW - 28, 80], radius=10, fill=NAVY_DEEP + (230,), outline=GOLD, width=2)
    img.alpha_composite(icon(40), (int(TW - bw - 18), 33))
    d.text((TW - bw + 34, 32), badge, font=fb, fill=WHITE)
    img.convert("RGB").save(out, quality=90)
    return out


SHORTS_W, SHORTS_H = 1080, 1920


def shorts_endcard(cover: Path | None, out: Path, lines: tuple[str, ...], transparency: float = 0.7) -> Path:
    """短影音結尾定格：封面鋪滿直式畫面並調成指定透明度（疊在品牌深藍上），中央放固定導流文字。"""
    img = _gradient((SHORTS_W, SHORTS_H))
    if cover and cover.exists():
        # 直式裁切會把封面標題切成半截，模糊後只留色調與氛圍，不和導流文字打架
        bg = _cover(Image.open(cover).convert("RGB"), (SHORTS_W, SHORTS_H)).filter(ImageFilter.GaussianBlur(28))
        img = Image.blend(img, bg, 1 - transparency)
    d = ImageDraw.Draw(img)
    f = _font(96)
    line_h = 140
    top = SHORTS_H // 2 - line_h * len(lines) // 2
    for i, line in enumerate(lines):
        y = top + i * line_h
        d.text(((SHORTS_W - f.getlength(line)) / 2, y), line, font=f, fill=WHITE, stroke_width=3, stroke_fill=NAVY_DEEP)
    rule_y = top + line_h * len(lines) + 40
    d.line([(SHORTS_W / 2 - 160, top - 50), (SHORTS_W / 2 + 160, top - 50)], fill=GOLD, width=4)
    cx = SHORTS_W / 2
    d.polygon([(cx - 46, rule_y), (cx + 46, rule_y), (cx, rule_y + 56)], fill=GOLD)
    img.save(out, quality=95)
    return out
