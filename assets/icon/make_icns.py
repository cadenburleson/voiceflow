#!/usr/bin/env python3
"""Turn a 1024x1024 icon render (squircle on white) into a macOS AppIcon.icns.

The generated art sits as a colored squircle on a white background; macOS doesn't
round .icns art itself, so we: detect the squircle, crop to it, apply a clean
superellipse alpha mask (transparent corners), inset it onto a transparent canvas
to match Apple's icon grid, then emit every required size and run iconutil.

    .venv/bin/python assets/icon/make_icns.py assets/icon/gemini/final_r1.png

Writes assets/icon/VoiceFlow.icns (and a VoiceFlow_1024.png master preview).
"""
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

CANVAS = 1024
INSET = 0.84          # squircle occupies ~84% of the canvas (Apple-ish padding)
CORNER = 0.2237       # superellipse corner radius as a fraction of side


def squircle_mask(size: int) -> Image.Image:
    # 4x supersample for smooth anti-aliased corners, then downscale.
    s = size * 4
    m = Image.new("L", (s, s), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * CORNER), fill=255)
    return m.resize((size, size), Image.LANCZOS)


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "assets/icon/gemini/final_r1.png")
    out_dir = src.parent.parent  # assets/icon
    im = Image.open(src).convert("RGBA")

    # Detect the colored squircle (non-near-white pixels) and crop to a square bbox.
    rgb = np.asarray(im)[:, :, :3].astype(int)
    colored = rgb.min(axis=2) <= 205
    ys, xs = np.where(colored)
    cx, cy = (xs.min() + xs.max()) // 2, (ys.min() + ys.max()) // 2
    half = (max(xs.max() - xs.min(), ys.max() - ys.min()) // 2) + 2
    crop = im.crop((cx - half, cy - half, cx + half, cy + half))

    # Size the squircle to the inset, mask its corners transparent.
    side = int(CANVAS * INSET)
    art = crop.resize((side, side), Image.LANCZOS)
    art.putalpha(squircle_mask(side))

    # Soft drop shadow on a transparent canvas, then the art on top.
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    off = (CANVAS - side) // 2
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    sh = Image.new("L", (side, side), 0)
    ImageDraw.Draw(sh).rounded_rectangle([0, 0, side - 1, side - 1], radius=int(side * CORNER), fill=90)
    shadow.paste((0, 0, 0, 90), (off, off + int(side * 0.02)), sh)
    shadow = shadow.filter(ImageFilter.GaussianBlur(CANVAS * 0.012))
    canvas = Image.alpha_composite(canvas, shadow)
    canvas.paste(art, (off, off), art)

    master = out_dir / "VoiceFlow_1024.png"
    canvas.save(master)

    # Build the .iconset with every required size, then iconutil -> .icns.
    iconset = out_dir / "VoiceFlow.iconset"
    iconset.mkdir(exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = base * scale
            name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
            canvas.resize((px, px), Image.LANCZOS).save(iconset / name)

    icns = out_dir / "VoiceFlow.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
    print(f"wrote {icns} and {master}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
