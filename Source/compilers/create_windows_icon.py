#!/usr/bin/env python3
"""
Create or validate a high-resolution Windows .ico file for MixSplitR.

Works with either Pillow or PySide6 as the image backend.
"""

from __future__ import annotations

import argparse
import io
import struct
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    try:
        from PySide6.QtSvg import QSvgRenderer
    except Exception:
        QSvgRenderer = None
    HAVE_QT = True
except Exception:
    HAVE_QT = False
    QImage = None
    QPainter = None
    Qt = None
    QRectF = None
    QBuffer = None
    QByteArray = None
    QIODevice = None
    QSvgRenderer = None


SOURCE_CANDIDATES = [
    "mixsplitr_icon_512.png",
    "mixsplitr.png",
    "icon.png",
]

REQUIRED_BASE_SIZES = {(16, 16), (32, 32), (48, 48)}
TARGET_ICO_SIZES = [
    (16, 16),
    (20, 20),
    (24, 24),
    (32, 32),
    (40, 40),
    (48, 48),
    (64, 64),
    (128, 128),
    (256, 256),
]


def _read_ico_sizes(path: Path) -> set[tuple[int, int]]:
    try:
        data = path.read_bytes()
    except OSError:
        return set()
    if len(data) < 6:
        return set()
    reserved, icon_type, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or icon_type != 1:
        return set()
    sizes: set[tuple[int, int]] = set()
    offset = 6
    for _ in range(count):
        if offset + 16 > len(data):
            break
        width, height, _, _, _, _, _, _ = struct.unpack_from("<BBBBHHII", data, offset)
        offset += 16
        w = 256 if width == 0 else int(width)
        h = 256 if height == 0 else int(height)
        sizes.add((w, h))
    return sizes


def _is_high_res_ico(path: Path) -> bool:
    sizes = _read_ico_sizes(path)
    if not sizes:
        return False
    if not REQUIRED_BASE_SIZES.issubset(sizes):
        return False
    return any(w >= 256 and h >= 256 for w, h in sizes)


def _select_source(source: str | None) -> Path | None:
    if source:
        p = Path(source)
        return p if p.exists() else None
    for candidate in SOURCE_CANDIDATES:
        p = Path(candidate)
        if p.exists():
            return p
    return None


def _write_ico_from_pngs(output: Path, png_entries: list[tuple[int, int, bytes]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = len(png_entries)
    header = struct.pack("<HHH", 0, 1, count)

    directory = bytearray()
    data_blob = bytearray()
    offset = 6 + (16 * count)

    for width, height, png_data in png_entries:
        w_byte = 0 if width >= 256 else width
        h_byte = 0 if height >= 256 else height
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                w_byte,
                h_byte,
                0,
                0,
                1,
                32,
                len(png_data),
                offset,
            )
        )
        data_blob.extend(png_data)
        offset += len(png_data)

    output.write_bytes(header + bytes(directory) + bytes(data_blob))


def _build_pillow_canvas(source: Path, size: int = 1024):
    if Image is None:
        raise RuntimeError("Pillow backend unavailable")
    img = Image.open(source).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    max_dim = int(size * 0.9)
    img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    x = (size - img.width) // 2
    y = (size - img.height) // 2
    canvas.paste(img, (x, y), img)
    return canvas


def _build_qt_canvas(source: Path, size: int = 1024):
    if not HAVE_QT:
        raise RuntimeError("PySide6 backend unavailable")

    canvas = QImage(size, size, QImage.Format_ARGB32)
    canvas.fill(0)

    if source.suffix.lower() == ".svg":
        if QSvgRenderer is None:
            raise RuntimeError("QtSvg is not available to render SVG icons")
        renderer = QSvgRenderer(str(source))
        if not renderer.isValid():
            raise RuntimeError(f"Invalid SVG file: {source}")
        painter = QPainter(canvas)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        return canvas

    src = QImage(str(source))
    if src.isNull():
        raise RuntimeError(f"Failed to load source image: {source}")
    if src.format() != QImage.Format_ARGB32:
        src = src.convertToFormat(QImage.Format_ARGB32)

    max_dim = int(size * 0.9)
    scaled = src.scaled(max_dim, max_dim, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    painter = QPainter(canvas)
    x = (size - scaled.width()) // 2
    y = (size - scaled.height()) // 2
    painter.drawImage(x, y, scaled)
    painter.end()
    return canvas


def _png_entries_from_pillow(source: Path) -> list[tuple[int, int, bytes]]:
    base = _build_pillow_canvas(source, size=1024)
    entries: list[tuple[int, int, bytes]] = []
    for width, height in TARGET_ICO_SIZES:
        img = base.resize((width, height), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        entries.append((width, height, buf.getvalue()))
    return entries


def _png_entries_from_qt(source: Path) -> list[tuple[int, int, bytes]]:
    base = _build_qt_canvas(source, size=1024)
    entries: list[tuple[int, int, bytes]] = []
    for width, height in TARGET_ICO_SIZES:
        img = base.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        payload = QByteArray()
        buffer = QBuffer(payload)
        if not buffer.open(QIODevice.WriteOnly):
            raise RuntimeError("Failed to open in-memory buffer for PNG encoding")
        if not img.save(buffer, "PNG"):
            buffer.close()
            raise RuntimeError(f"Failed to encode {width}x{height} PNG frame")
        buffer.close()
        entries.append((width, height, bytes(payload)))
    return entries


def ensure_windows_icon(output: Path, source: str | None, force: bool = False) -> int:
    if output.exists() and _is_high_res_ico(output) and not force:
        sizes = sorted(_read_ico_sizes(output))
        print(f"[OK] Existing icon is valid: {output}")
        print(f"  Sizes: {sizes}")
        return 0

    source_path = _select_source(source)
    if not source_path:
        print("[ERROR] No source image found. Expected one of:")
        for candidate in SOURCE_CANDIDATES:
            print(f"   - {candidate}")
        if source:
            print(f"   - {source} (requested)")
        return 1

    try:
        if Image is not None:
            print(f"[INFO] Generating Windows icon from: {source_path} (Pillow backend)")
            entries = _png_entries_from_pillow(source_path)
        elif HAVE_QT:
            print(f"[INFO] Generating Windows icon from: {source_path} (PySide6 backend)")
            entries = _png_entries_from_qt(source_path)
        else:
            print("[ERROR] Neither Pillow nor PySide6 is available for icon generation")
            return 1
        _write_ico_from_pngs(output, entries)
    except Exception as exc:
        print(f"[ERROR] Failed to generate icon: {exc}")
        return 1

    if not _is_high_res_ico(output):
        print(f"[ERROR] Generated icon is missing required resolutions: {output}")
        return 1

    sizes = sorted(_read_ico_sizes(output))
    print(f"[OK] Windows icon ready: {output}")
    print(f"   Sizes: {sizes}")
    return 0


def check_windows_icon(path: Path) -> int:
    if _is_high_res_ico(path):
        sizes = sorted(_read_ico_sizes(path))
        print(f"[OK] Valid multi-resolution icon: {path}")
        print(f"  Sizes: {sizes}")
        return 0
    print(f"[ERROR] Invalid/low-resolution icon: {path}")
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or validate Windows icon.ico")
    parser.add_argument("output", nargs="?", default="icon.ico", help="Output .ico path")
    parser.add_argument("--source", default=None, help="Source PNG/JPG/SVG image")
    parser.add_argument("--force", action="store_true", help="Regenerate even if icon already looks valid")
    parser.add_argument("--check", metavar="ICO_PATH", help="Check icon quality only")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.check:
        return check_windows_icon(Path(args.check))
    return ensure_windows_icon(Path(args.output), args.source, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
