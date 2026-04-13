#!/usr/bin/env python3
"""
DMG background/icon asset generator for MixSplitR.

Creates:
- background image for create-dmg
- dmg icon (copied from app icon when available)

Requirements: pillow
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("❌ Pillow is not installed. Run: python3 -m pip install pillow")
    sys.exit(1)


def _load_font(size: int) -> ImageFont.ImageFont:
    font_candidates = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]
    for font_path in font_candidates:
        if Path(font_path).exists():
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def create_dmg_background(
    output: str,
    app_name: str,
    version: str,
    width: int = 600,
    height: int = 450,
) -> str:
    img = Image.new("RGB", (width, height), (245, 247, 250))
    draw = ImageDraw.Draw(img)

    # Subtle vertical gradient.
    for y in range(height):
        ratio = y / max(1, height - 1)
        r = int(242 - 18 * ratio)
        g = int(246 - 22 * ratio)
        b = int(250 - 28 * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Gentle texture noise for a less flat background.
    random.seed(42)
    for _ in range(120):
        x = random.randint(0, width)
        y = random.randint(0, height)
        tone = random.randint(230, 250)
        draw.ellipse([x, y, x + 2, y + 2], fill=(tone, tone, tone))

    # Header band.
    header_h = 68
    for y in range(header_h):
        ratio = y / max(1, header_h - 1)
        r = int(24 + 40 * ratio)
        g = int(110 + 55 * ratio)
        b = int(204 + 32 * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    title_font = _load_font(28)
    subtitle_font = _load_font(14)
    instruction_font = _load_font(17)

    title = f"{app_name} v{version}"
    title_box = draw.textbbox((0, 0), title, font=title_font)
    title_w = title_box[2] - title_box[0]
    title_x = (width - title_w) // 2
    draw.text((title_x + 1, 14), title, fill=(12, 30, 52), font=title_font)
    draw.text((title_x, 13), title, fill=(255, 255, 255), font=title_font)

    subtitle = "Drag the app to Applications"
    subtitle_box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    subtitle_w = subtitle_box[2] - subtitle_box[0]
    subtitle_x = (width - subtitle_w) // 2
    draw.text((subtitle_x, 45), subtitle, fill=(232, 242, 255), font=subtitle_font)

    instruction = "Install by dragging MixSplitR.app into Applications"
    inst_box = draw.textbbox((0, 0), instruction, font=instruction_font)
    inst_w = inst_box[2] - inst_box[0]
    inst_x = (width - inst_w) // 2
    inst_y = height // 2 + 30
    draw.text((inst_x + 1, inst_y + 1), instruction, fill=(180, 186, 194), font=instruction_font)
    draw.text((inst_x, inst_y), instruction, fill=(82, 88, 98), font=instruction_font)

    # Direction arrow.
    arrow_y = height // 2 + 8
    arrow_start_x = 235
    arrow_end_x = 365
    arrow_color = (28, 126, 220)
    for offset in range(-2, 3):
        draw.line(
            [(arrow_start_x, arrow_y + offset), (arrow_end_x, arrow_y + offset)],
            fill=arrow_color,
            width=1,
        )
    draw.polygon(
        [
            (arrow_end_x, arrow_y),
            (arrow_end_x - 16, arrow_y - 11),
            (arrow_end_x - 16, arrow_y + 11),
        ],
        fill=arrow_color,
    )

    # Placement guides for app and Applications icons.
    app_circle = (175, height // 2 - 28)
    apps_circle = (425, height // 2 - 28)
    draw.ellipse(
        [app_circle[0] - 55, app_circle[1] - 55, app_circle[0] + 55, app_circle[1] + 55],
        outline=(194, 204, 220),
        width=2,
    )
    draw.ellipse(
        [apps_circle[0] - 55, apps_circle[1] - 55, apps_circle[0] + 55, apps_circle[1] + 55],
        outline=(194, 204, 220),
        width=2,
    )

    footer = f"© {datetime.now().year} {app_name}"
    footer_box = draw.textbbox((0, 0), footer, font=subtitle_font)
    footer_w = footer_box[2] - footer_box[0]
    footer_x = (width - footer_w) // 2
    draw.text((footer_x, height - 30), footer, fill=(146, 152, 162), font=subtitle_font)

    img.save(output, "PNG")
    print(f"✅ DMG background created: {output}")
    return output


def create_dmg_icon_from_app_icon(app_icon: str, output: str) -> bool:
    src = Path(app_icon)
    dst = Path(output)
    if not src.exists():
        print(f"⚠️  App icon not found: {app_icon}")
        return False

    shutil.copy(src, dst)
    print(f"✅ DMG icon created: {output}")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create DMG background and icon assets.")
    parser.add_argument("background", nargs="?", default="background.png", help="Output background PNG path")
    parser.add_argument("dmg_icon", nargs="?", default="dmg_icon.icns", help="Output DMG icon path")
    parser.add_argument("--app-icon", default="icon.icns", help="Source app icon path used to create dmg icon")
    parser.add_argument("--name", default="MixSplitR", help="Application name")
    parser.add_argument(
        "--version",
        default=os.getenv("MIXSPLITR_VERSION", "8.0"),
        help="Version text shown in the background title",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("🎨 Creating DMG assets...\n")
    create_dmg_background(args.background, args.name, args.version)

    if not create_dmg_icon_from_app_icon(args.app_icon, args.dmg_icon):
        print("   Run create_icon.py first if you need a custom DMG icon.")

    print("\n✅ DMG assets ready!")
    print(f"   Background: {args.background}")
    if Path(args.dmg_icon).exists():
        print(f"   DMG Icon: {args.dmg_icon}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
