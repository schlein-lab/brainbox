#!/usr/bin/env python3

import sys
from PIL import Image, ImageDraw

BG    = (20, 20, 34, 0)
TILE  = (107, 124, 255, 255)
QUIET = (169, 169, 196, 255)

def render(sz):
    img = Image.new("RGBA", (sz, sz), BG)
    d = ImageDraw.Draw(img)
    pad = max(2, sz // 8)
    gap = max(2, sz // 12)
    cell = (sz - 2 * pad - 2 * gap) // 3
    r = max(1, cell // 5)
    for gy in range(3):
        for gx in range(3):
            x = pad + gx * (cell + gap)
            y = pad + gy * (cell + gap)
            col = QUIET if (gx == 2 and gy == 2) else TILE
            d.rounded_rectangle([x, y, x + cell, y + cell], radius=r, fill=col)
    return img

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "apps-icon.png"
    sz = int(sys.argv[2]) if len(sys.argv) > 2 else 48
    render(sz).save(out, "PNG")
    print(out)

if __name__ == "__main__":
    main()
