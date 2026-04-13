#!/usr/bin/env python3
"""
Create a macOS .icns file for MixSplitR.

Behavior:
- Uses a provided source image when available.
- Falls back to a generated placeholder icon when no source image exists.

Requirements: pillow
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("❌ Pillow not installed. Run: python3 -m pip install pillow")
    sys.exit(1)


SOURCE_CANDIDATES = [
    "mixsplitr_icon_512.png",
    "mixsplitr.png",
    "icon.png",
]


def _load_font(size: int) -> ImageFont.ImageFont:
    for font_path in (
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Bold.ttf",
    ):
        if Path(font_path).exists():
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def create_fallback_icon(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded square background with blue gradient.
    radius = size // 5
    for i in range(size):
        ratio = i / max(1, size - 1)
        color = (
            int(18 + 42 * ratio),
            int(112 + 60 * ratio),
            int(206 + 35 * ratio),
            255,
        )
        draw.rounded_rectangle([0, i, size, i + 1], radius=radius, fill=color)

    text = "MS"
    font = _load_font(size // 3)
    box = draw.textbbox((0, 0), text, font=font)
    tw = box[2] - box[0]
    th = box[3] - box[1]
    tx = (size - tw) // 2
    ty = (size - th) // 2 - size // 20

    draw.text((tx + size // 100, ty + size // 100), text, fill=(8, 26, 46, 180), font=font)
    draw.text((tx, ty), text, fill=(245, 250, 255, 255), font=font)
    return img


def load_source_icon(source: str | None, size: int = 1024) -> Image.Image:
    source_path = Path(source) if source else None

    if source_path and source_path.exists():
        img = Image.open(source_path).convert("RGBA")
        print(f"✅ Using source image: {source_path}")
    else:
        auto_source = next((Path(p) for p in SOURCE_CANDIDATES if Path(p).exists()), None)
        if auto_source:
            img = Image.open(auto_source).convert("RGBA")
            print(f"✅ Using source image: {auto_source}")
        else:
            print("⚠️  No source icon image found; generating fallback icon")
            return create_fallback_icon(size)

    # Fit source art into a square with transparent padding.
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    max_dim = int(size * 0.9)
    img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    x = (size - img.width) // 2
    y = (size - img.height) // 2
    canvas.paste(img, (x, y), img)
    return canvas


def create_icns(output_path: str = "icon.icns", source_image: str | None = None) -> bool:
    print("🎨 Creating icon set...")

    base = load_source_icon(source_image, size=1024)

    iconset_dir = Path("icon.iconset")
    if iconset_dir.exists():
        shutil.rmtree(iconset_dir)
    iconset_dir.mkdir(parents=True)

    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for size in sizes:
        img = base.resize((size, size), Image.Resampling.LANCZOS)
        img.save(iconset_dir / f"icon_{size}x{size}.png")
        print(f"  ✓ icon_{size}x{size}.png")

        if size <= 512:
            img2 = base.resize((size * 2, size * 2), Image.Resampling.LANCZOS)
            img2.save(iconset_dir / f"icon_{size}x{size}@2x.png")
            print(f"  ✓ icon_{size}x{size}@2x.png")

    try:
        subprocess.run(["iconutil", "-c", "icns", str(iconset_dir), "-o", output_path], check=True)
    except FileNotFoundError:
        print("❌ iconutil not found (macOS only)")
        print(f"   PNG iconset preserved at: {iconset_dir}")
        return False
    except subprocess.CalledProcessError:
        print("❌ iconutil failed to generate .icns")
        print(f"   PNG iconset preserved at: {iconset_dir}")
        return False

    print(f"✅ Icon created: {output_path}")
    shutil.rmtree(iconset_dir)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create icon.icns for MixSplitR")
    parser.add_argument("output", nargs="?", default="icon.icns", help="Output .icns path")
    parser.add_argument("--source", default=None, help="Source PNG/JPG image to convert")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    success = create_icns(args.output, args.source)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
