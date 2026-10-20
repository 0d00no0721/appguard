"""生成 AppGuard 图标（深色科技风：紫罗兰 -> 天蓝 -> 亮蓝渐变）。

设计参考：深色圆角方形底 + 同色系渐变的抽象 A/层叠线条 + 亮蓝圆点。
用法：`python tools\\make_icon.py`
输出：`ui/assets/appguard.png`、`appguard-64.png`、`appguard-256.png`、`appguard.ico`
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "ui", "assets")

PURPLE = (106, 92, 255)
BLUE = (46, 134, 193)
LIGHT = (91, 192, 235)
DARK_TOP = (56, 56, 56)
DARK_BOTTOM = (34, 34, 34)
BORDER = (69, 69, 69)


def _gradient(size: int, stops: list[tuple[float, tuple[int, int, int]]]) -> Image.Image:
    img = Image.new("RGB", (1, size))
    pixels = img.load()
    for y in range(size):
        t = y / max(1, size - 1)
        for index in range(len(stops) - 1):
            p0, c0 = stops[index]
            p1, c1 = stops[index + 1]
            if p0 <= t <= p1:
                k = (t - p0) / max(1e-6, p1 - p0)
                pixels[0, y] = tuple(int(c0[i] + (c1[i] - c0[i]) * k) for i in range(3))
                break
        else:
            pixels[0, y] = stops[-1][1]
    return img.resize((size, size))


def draw(size: int = 1024) -> Image.Image:
    s = size / 1024.0

    def pt(x, y):
        return (int(round(x * s)), int(round(y * s)))

    # 深色圆角底（轻微纵向渐变）
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg = _gradient(size, [(0.0, DARK_TOP), (1.0, DARK_BOTTOM)]).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(220 * s), fill=255)
    base.paste(bg, (0, 0), mask)
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((1, 1, size - 2, size - 2), radius=int(220 * s),
                           outline=BORDER, width=max(2, int(4 * s)))

    # 渐变（紫 -> 蓝 -> 亮蓝），按 y 轴
    grad = _gradient(size, [(0.0, PURPLE), (0.52, BLUE), (1.0, LIGHT)]).convert("RGBA")

    # “A” 形：两条斜向圆角粗线 + 横梁；用 mask + 渐变填充
    glyph = Image.new("L", (size, size), 0)
    gd = ImageDraw.Draw(glyph)
    width = int(118 * s)
    for start, end in (((300, 800), (500, 250)), ((520, 250), (720, 800))):
        gd.line([pt(*start), pt(*end)], fill=255, width=width)
        for x, y in (start, end):
            r = width // 2
            gd.ellipse((pt(x, y)[0] - r, pt(x, y)[1] - r,
                        pt(x, y)[0] + r, pt(x, y)[1] + r), fill=255)
    # 横梁
    gd.line([pt(410, 570), pt(610, 570)], fill=255, width=int(76 * s))
    for x in (410, 610):
        r = int(38 * s)
        gd.ellipse((pt(x, 570)[0] - r, pt(x, 570)[1] - r,
                    pt(x, 570)[0] + r, pt(x, 570)[1] + r), fill=255)

    # 轻微辉光（现代深色图标常见）
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow.paste(grad, (0, 0), glyph)
    glow = glow.filter(ImageFilter.GaussianBlur(int(42 * s)))
    base.alpha_composite(glow, (0, 0))
    base.paste(grad, (0, 0), glyph)

    # 亮蓝圆点（参考 Logo 的底部圆点）
    dot = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dot)
    r = int(58 * s)
    cx, cy = pt(770, 300)
    dd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=LIGHT + (255,))
    base.alpha_composite(dot)
    return base


def main() -> int:
    os.makedirs(ASSETS, exist_ok=True)
    master = draw(1024)
    master.save(os.path.join(ASSETS, "appguard.png"))
    master.resize((256, 256), Image.LANCZOS).save(
        os.path.join(ASSETS, "appguard-256.png"))
    master.resize((64, 64), Image.LANCZOS).save(
        os.path.join(ASSETS, "appguard-64.png"))
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
             (128, 128), (256, 256)]
    master.save(os.path.join(ASSETS, "appguard.ico"), sizes=sizes)
    master.save(os.path.join(ROOT, "appguard.ico"), sizes=sizes)
    print("icon written:", ASSETS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
