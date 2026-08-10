#!/usr/bin/env python3

import sys
from PIL import Image, ImageDraw, ImageFont

BG_TOP = (13, 13, 22)
BG_BOT = (18, 18, 30)
INK = (58, 58, 92)
ACCENT = (107, 124, 255)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def render(w, h):
    img = Image.new("RGB", (w, h), BG_TOP)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT)))

    s = max(1, w // 480)
    gw = w // 6
    cx, cy = w // 2, int(h * 0.44)
    bw, bh = gw, int(gw * 0.62)
    box = [cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2]
    d.rounded_rectangle(box, radius=bh // 5, outline=INK, width=2 * s)
    nodes = [(cx - bw // 4, cy + bh // 6), (cx, cy - bh // 5), (cx + bw // 4, cy + bh // 8)]
    for a, b in ((0, 1), (1, 2)):
        d.line([nodes[a], nodes[b]], fill=INK, width=2 * s)
    r = 3 * s
    for i, (nx, ny) in enumerate(nodes):
        col = ACCENT if i == 1 else INK
        d.ellipse([nx - r, ny - r, nx + r, ny + r], fill=col)

    try:
        f = ImageFont.truetype(FONT, size=max(14, gw // 7))
    except OSError:
        f = ImageFont.load_default()
    text = "brainbox"
    tw = d.textlength(text, font=f)
    d.text((cx - tw / 2, box[3] + bh // 4), text, font=f, fill=INK)
    return img

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "brainbox-wallpaper.png"
    size = sys.argv[2] if len(sys.argv) > 2 else "1920x1080"
    w, h = (int(x) for x in size.lower().split("x"))
    render(w, h).save(out, "PNG", optimize=True)
    print(out)

if __name__ == "__main__":
    main()
