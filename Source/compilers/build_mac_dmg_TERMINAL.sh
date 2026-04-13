#!/bin/bash
# MixSplitR macOS Build Script (App Bundle + DMG)
#
# Usage:
#   ./build_mac_dmg_TERMINAL.sh
#   ./build_mac_dmg_TERMINAL.sh --skip-deps
#   ./build_mac_dmg_TERMINAL.sh --skip-sign
#   ./build_mac_dmg_TERMINAL.sh --spec Custom.spec
#   ./build_mac_dmg_TERMINAL.sh --target-arch universal2

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

APP_NAME="MixSplitR"
APP_BUNDLE="${APP_NAME}.app"
BUNDLE_ID="com.mixsplitr.app"
MIN_MACOS="10.13"
SPEC_FILE="$SCRIPT_DIR/MixSplitR_ONEFILE.spec"
SKIP_DEPS=false
SKIP_SIGN=false
TARGET_ARCH="auto"
PYI_WORK_ROOT=""

cleanup_pyi_work_root() {
    if [[ -n "${PYI_WORK_ROOT:-}" && -d "${PYI_WORK_ROOT:-}" ]]; then
        rm -rf "${PYI_WORK_ROOT}" || true
    fi
}

trap cleanup_pyi_work_root EXIT

usage() {
    cat <<'USAGE'
Usage: ./build_mac_dmg_TERMINAL.sh [options]

Options:
  --skip-deps       Skip Homebrew/pip installation
  --skip-sign       Skip codesigning
  --target-arch     PyInstaller target architecture: auto | arm64 | x86_64 | universal2
  --spec <file>     Use a specific PyInstaller spec file
  -h, --help        Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-deps)
            SKIP_DEPS=true
            shift
            ;;
        --skip-sign)
            SKIP_SIGN=true
            shift
            ;;
        --target-arch)
            if [[ $# -lt 2 ]]; then
                echo "❌ --target-arch requires: auto | arm64 | x86_64 | universal2"
                exit 1
            fi
            case "$2" in
                auto|arm64|x86_64|universal2)
                    TARGET_ARCH="$2"
                    ;;
                *)
                    echo "❌ Invalid --target-arch value: $2"
                    echo "   Valid values: auto | arm64 | x86_64 | universal2"
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        --spec)
            if [[ $# -lt 2 ]]; then
                echo "❌ --spec requires a file path"
                exit 1
            fi
            SPEC_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "❌ Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
done

if [[ "$SPEC_FILE" != /* ]]; then
    if [[ -f "$SCRIPT_DIR/$SPEC_FILE" ]]; then
        SPEC_FILE="$SCRIPT_DIR/$SPEC_FILE"
    elif [[ -f "$PROJECT_ROOT/$SPEC_FILE" ]]; then
        SPEC_FILE="$PROJECT_ROOT/$SPEC_FILE"
    fi
fi

if [[ ! -f "$SPEC_FILE" ]]; then
    echo "❌ Spec file not found: $SPEC_FILE"
    exit 1
fi

resolve_version() {
    if [[ -n "${MIXSPLITR_VERSION:-}" ]]; then
        echo "$MIXSPLITR_VERSION"
        return
    fi

    local candidates=(
        "$PROJECT_ROOT/mixsplitr_core.py"
        "$SCRIPT_DIR/../../Source files 7.2/mixsplitr_core.py"
    )
    local candidate version

    for candidate in "${candidates[@]}"; do
        if [[ -f "$candidate" ]]; then
            version="$(grep -E "CURRENT_VERSION\s*=\s*['\"][^'\"]+['\"]" "$candidate" | head -1 | sed -E "s/.*['\"]([^'\"]+)['\"].*/\1/" || true)"
            if [[ -n "$version" ]]; then
                echo "$version"
                return
            fi
        fi
    done

    echo "8.0"
}

APP_VERSION="$(resolve_version)"
DMG_NAME="${APP_NAME}-v${APP_VERSION}.dmg"

resolve_build_mode() {
    local mode="terminal"

    if grep -Eq "main_ui\.py" "$SPEC_FILE"; then
        mode="gui"
    fi

    if grep -Eq "console\s*=\s*False|console=False" "$SPEC_FILE"; then
        mode="gui"
    fi

    echo "$mode"
}

BUILD_MODE="$(resolve_build_mode)"

resolve_requirements_file() {
    local candidates=(
        "$PROJECT_ROOT/requirements.txt"
        "$SCRIPT_DIR/../../Source files 7.2/requirements.txt"
    )
    local path

    for path in "${candidates[@]}"; do
        if [[ -f "$path" ]]; then
            echo "$path"
            return
        fi
    done
}

REQUIREMENTS_FILE="$(resolve_requirements_file || true)"
ARCH_DOC="$PROJECT_ROOT/ARCHITECTURE.md"

create_basic_dmg_with_icon() {
    local staging_dir="$1"
    local volume_name="$2"
    local final_dmg="$3"
    local icon_file="$4"
    local rw_dmg="${final_dmg%.dmg}.rw.dmg"

    rm -f "$rw_dmg" "$final_dmg"

    hdiutil create -volname "$volume_name" \
        -srcfolder "$staging_dir" \
        -ov -format UDRW \
        "$rw_dmg"

    if [[ -f "$icon_file" ]]; then
        if command -v SetFile >/dev/null 2>&1; then
            echo "   Applying custom volume icon..."
            local attach_line dev mount_point
            attach_line="$( (hdiutil attach "$rw_dmg" -readwrite -noverify -noautoopen 2>/dev/null || true) | awk '/^\/dev\// {line=$0} END{print line}' )"
            dev="$(echo "$attach_line" | awk '{print $1}')"
            mount_point="$(echo "$attach_line" | awk '{$1=$2=""; sub(/^ +/, ""); print}')"

            if [[ -n "$dev" && -n "$mount_point" && -d "$mount_point" ]]; then
                cp "$icon_file" "$mount_point/.VolumeIcon.icns"
                SetFile -c icnC "$mount_point/.VolumeIcon.icns" 2>/dev/null || true
                SetFile -a C "$mount_point" 2>/dev/null || true
                sync
                hdiutil detach "$dev" -quiet || hdiutil detach "$dev" -force -quiet || true
            else
                echo "   ⚠️  Could not mount RW DMG to apply volume icon"
            fi
        else
            echo "   ⚠️  SetFile not found; skipping volume icon flag (install Xcode CLT)"
        fi
    fi

    hdiutil convert "$rw_dmg" -ov -format UDZO -o "$final_dmg" >/dev/null
    rm -f "$rw_dmg"
}

apply_dmg_file_icon() {
    local dmg_file="$1"
    local icon_file="$2"

    if [[ ! -f "$dmg_file" || ! -f "$icon_file" ]]; then
        return 0
    fi

    if command -v sips >/dev/null 2>&1 && command -v DeRez >/dev/null 2>&1 && command -v Rez >/dev/null 2>&1 && command -v SetFile >/dev/null 2>&1; then
        local tmp_rsrc
        tmp_rsrc="$(mktemp /tmp/mixsplitr_dmg_icon.XXXXXX.rsrc)"
        sips -i "$icon_file" >/dev/null 2>&1 || true
        DeRez -only icns "$icon_file" > "$tmp_rsrc" 2>/dev/null || true
        if [[ -s "$tmp_rsrc" ]]; then
            Rez -append "$tmp_rsrc" -o "$dmg_file" 2>/dev/null || true
            SetFile -a C "$dmg_file" 2>/dev/null || true
            echo "   ✅ Applied custom icon to DMG file"
        fi
        rm -f "$tmp_rsrc"
    else
        echo "   ⚠️  DeRez/Rez/SetFile not found; skipping DMG file icon"
    fi
}

python_has_module() {
    local module="$1"
    python3 -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$module') else 1)" >/dev/null 2>&1
}

generate_icon_icns_inline() {
    local output_icns="$1"
    local source_image="${2:-}"

    MIXSPLITR_ICON_OUTPUT="$output_icns" MIXSPLITR_ICON_SOURCE="$source_image" python3 - <<'PY'
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    print("Pillow missing")
    sys.exit(1)

output = Path(os.environ.get("MIXSPLITR_ICON_OUTPUT", "icon.icns"))
source = os.environ.get("MIXSPLITR_ICON_SOURCE", "").strip()
source_candidates = [source] if source else []
source_candidates += ["mixsplitr_icon_512.png", "mixsplitr.png", "icon.png"]

def load_font(size: int):
    for path in (
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Bold.ttf",
    ):
        p = Path(path)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                pass
    return ImageFont.load_default()

def fallback_icon(size: int = 1024):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = size // 5
    for y in range(size):
        r = y / max(1, size - 1)
        color = (int(18 + 42 * r), int(112 + 60 * r), int(206 + 35 * r), 255)
        draw.rounded_rectangle([0, y, size, y + 1], radius=radius, fill=color)
    text = "MS"
    font = load_font(size // 3)
    box = draw.textbbox((0, 0), text, font=font)
    tx = (size - (box[2] - box[0])) // 2
    ty = (size - (box[3] - box[1])) // 2 - size // 20
    draw.text((tx + size // 100, ty + size // 100), text, fill=(8, 26, 46, 180), font=font)
    draw.text((tx, ty), text, fill=(245, 250, 255, 255), font=font)
    return img

base = None
for cand in source_candidates:
    if not cand:
        continue
    p = Path(cand)
    if p.exists():
        try:
            src = Image.open(p).convert("RGBA")
            canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
            src.thumbnail((920, 920), Image.Resampling.LANCZOS)
            x = (1024 - src.width) // 2
            y = (1024 - src.height) // 2
            canvas.paste(src, (x, y), src)
            base = canvas
            print(f"Using source image: {p}")
            break
        except Exception:
            pass

if base is None:
    print("No source image found; generating fallback icon")
    base = fallback_icon(1024)

iconutil = shutil.which("iconutil")
if not iconutil:
    print("iconutil not found")
    sys.exit(1)

iconset_dir = Path(tempfile.mkdtemp(prefix="mixsplitr_iconset_")) / "icon.iconset"
iconset_dir.mkdir(parents=True, exist_ok=True)

for size in [16, 32, 64, 128, 256, 512, 1024]:
    img = base.resize((size, size), Image.Resampling.LANCZOS)
    img.save(iconset_dir / f"icon_{size}x{size}.png")
    if size <= 512:
        img2 = base.resize((size * 2, size * 2), Image.Resampling.LANCZOS)
        img2.save(iconset_dir / f"icon_{size}x{size}@2x.png")

subprocess.run([iconutil, "-c", "icns", str(iconset_dir), "-o", str(output)], check=True)
print(f"Generated {output}")
PY
}

generate_dmg_background_inline() {
    local output_png="$1"
    local app_name="$2"
    local app_version="$3"

    MIXSPLITR_DMG_BG_OUT="$output_png" MIXSPLITR_DMG_NAME="$app_name" MIXSPLITR_DMG_VERSION="$app_version" python3 - <<'PY'
import os
import random
import sys
from datetime import datetime

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    print("Pillow missing")
    sys.exit(1)

output = os.environ.get("MIXSPLITR_DMG_BG_OUT", "background.png")
app_name = os.environ.get("MIXSPLITR_DMG_NAME", "MixSplitR")
version = os.environ.get("MIXSPLITR_DMG_VERSION", "8.0")
width, height = 600, 450

img = Image.new("RGB", (width, height), (245, 247, 250))
draw = ImageDraw.Draw(img)

for y in range(height):
    r = y / max(1, height - 1)
    draw.line([(0, y), (width, y)], fill=(int(242 - 18 * r), int(246 - 22 * r), int(250 - 28 * r)))

random.seed(42)
for _ in range(120):
    x = random.randint(0, width)
    y = random.randint(0, height)
    tone = random.randint(230, 250)
    draw.ellipse([x, y, x + 2, y + 2], fill=(tone, tone, tone))

header_h = 68
for y in range(header_h):
    r = y / max(1, header_h - 1)
    draw.line([(0, y), (width, y)], fill=(int(24 + 40 * r), int(110 + 55 * r), int(204 + 32 * r)))

def load_font(size: int):
    for fp in (
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except OSError:
                pass
    return ImageFont.load_default()

title_font = load_font(28)
subtitle_font = load_font(14)
inst_font = load_font(17)

title = f"{app_name} v{version}"
tb = draw.textbbox((0, 0), title, font=title_font)
tx = (width - (tb[2] - tb[0])) // 2
draw.text((tx + 1, 14), title, fill=(12, 30, 52), font=title_font)
draw.text((tx, 13), title, fill=(255, 255, 255), font=title_font)

subtitle = "Drag the app to Applications"
sb = draw.textbbox((0, 0), subtitle, font=subtitle_font)
sx = (width - (sb[2] - sb[0])) // 2
draw.text((sx, 45), subtitle, fill=(232, 242, 255), font=subtitle_font)

instruction = "Install by dragging MixSplitR.app into Applications"
ib = draw.textbbox((0, 0), instruction, font=inst_font)
ix = (width - (ib[2] - ib[0])) // 2
iy = height // 2 + 30
draw.text((ix + 1, iy + 1), instruction, fill=(180, 186, 194), font=inst_font)
draw.text((ix, iy), instruction, fill=(82, 88, 98), font=inst_font)

arrow_y = height // 2 + 8
arrow_start_x, arrow_end_x = 235, 365
arrow_color = (28, 126, 220)
for off in range(-2, 3):
    draw.line([(arrow_start_x, arrow_y + off), (arrow_end_x, arrow_y + off)], fill=arrow_color, width=1)
draw.polygon([(arrow_end_x, arrow_y), (arrow_end_x - 16, arrow_y - 11), (arrow_end_x - 16, arrow_y + 11)], fill=arrow_color)

for cx in (175, 425):
    cy = height // 2 - 28
    draw.ellipse([cx - 55, cy - 55, cx + 55, cy + 55], outline=(194, 204, 220), width=2)

footer = f"© {datetime.now().year} {app_name}"
fb = draw.textbbox((0, 0), footer, font=subtitle_font)
fx = (width - (fb[2] - fb[0])) // 2
draw.text((fx, height - 30), footer, fill=(146, 152, 162), font=subtitle_font)

img.save(output, "PNG")
print(f"Generated {output}")
PY
}

install_brew_packages() {
    local packages=("ffmpeg" "chromaprint" "create-dmg" "libdiscid" "portaudio" "pkg-config")

    if ! command -v brew >/dev/null 2>&1; then
        echo "   ⚠️  Homebrew not found. Skipping brew installs."
        return 0
    fi

    local pkg
    for pkg in "${packages[@]}"; do
        if brew list "$pkg" >/dev/null 2>&1; then
            echo "   ✅ $pkg already installed"
        else
            echo "   📥 Installing $pkg..."
            brew install "$pkg" || echo "   ⚠️  Failed to install $pkg (continuing)"
        fi
    done
}

install_python_packages() {
    echo "   🐍 Upgrading pip/setuptools/wheel..."
    python3 -m pip install --upgrade pip setuptools wheel
    python3 -m pip install --upgrade pyinstaller pillow

    if [[ -n "$REQUIREMENTS_FILE" ]]; then
        echo "   📄 Installing from requirements: $REQUIREMENTS_FILE"
        python3 -m pip install -r "$REQUIREMENTS_FILE"
    else
        echo "   ⚠️  requirements.txt not found. Installing architecture-aligned fallback packages..."
        python3 -m pip install \
            PySide6 pydub mutagen requests tqdm \
            musicbrainzngs pyacoustid shazamio discid \
            librosa numpy scipy numba \
            soundfile soundcard sounddevice \
            prompt_toolkit wcwidth psutil \
            aiohttp aiosignal frozenlist multidict yarl async_timeout attrs charset_normalizer \
            flask werkzeug
    fi

    python3 -m pip install acrcloud || echo "   ⚠️  acrcloud unavailable on this environment (optional)"
    python3 -m pip install essentia || echo "   ⚠️  essentia unavailable on this environment (optional)"
}

verify_prerequisites() {
    local missing=false
    local cmd

    if ! python3 -m PyInstaller --version >/dev/null 2>&1; then
        echo "   ❌ PyInstaller not found"
        missing=true
    fi

    for cmd in ffmpeg ffprobe fpcalc; do
        if command -v "$cmd" >/dev/null 2>&1; then
            echo "   ✅ $cmd"
        else
            echo "   ❌ $cmd not found"
            missing=true
        fi
    done

    local required_modules=(
        "pydub"
        "mutagen"
        "requests"
        "tqdm"
        "musicbrainzngs"
        "acoustid"
        "shazamio"
        "numpy"
        "scipy"
        "soundfile"
        "prompt_toolkit"
        "wcwidth"
        "psutil"
        "aiohttp"
    )

    if [[ "$BUILD_MODE" == "gui" ]]; then
        required_modules+=("PySide6")
    else
        required_modules+=("flask" "werkzeug")
    fi

    local optional_modules=(
        "acrcloud"
        "librosa"
        "discid"
        "essentia"
        "numba"
        "soundcard"
        "sounddevice"
        "PIL"
    )
    if [[ "$OSTYPE" == darwin* || "$TARGET_OS" == "darwin" ]]; then
        optional_modules+=("objc" "AppKit")
    fi
    if [[ "$BUILD_MODE" == "gui" ]]; then
        optional_modules+=("flask" "werkzeug")
    fi

    local module
    for module in "${required_modules[@]}"; do
        if python_has_module "$module"; then
            echo "   ✅ $module"
        else
            echo "   ❌ $module missing"
            missing=true
        fi
    done

    for module in "${optional_modules[@]}"; do
        if python_has_module "$module"; then
            echo "   ✅ $module (optional)"
        else
            echo "   ⚠️  $module not installed (feature may be disabled)"
        fi
    done

    if [[ "$missing" == true ]]; then
        echo ""
        echo "❌ Missing required prerequisites."
        echo "   Re-run without --skip-deps, or install the missing dependencies manually."
        exit 1
    fi
}

create_terminal_launcher() {
    local launcher_path="$1"
    local runner_binary="$2"

    cat > "$launcher_path" <<'EOFLAUNCH'
#!/bin/bash
APP_DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
APP_BIN="$APP_DIR/MixSplitR"

if [[ ! -x "$APP_BIN" ]]; then
    /usr/bin/osascript -e 'display dialog "MixSplitR executable not found in app bundle resources." buttons {"OK"} default button 1 with icon caution'
    exit 1
fi

TEMP_SCRIPT="$(mktemp /tmp/mixsplitr_terminal_runner.XXXXXX.sh)"
cat > "$TEMP_SCRIPT" <<'EOFSCRIPT'
#!/bin/bash
clear
echo "════════════════════════════════════════════════════════"
echo "  Starting MixSplitR..."
echo "════════════════════════════════════════════════════════"
echo ""

APP_BIN="$1"
"$APP_BIN"
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "════════════════════════════════════════════════════════"
    echo "  MixSplitR exited with error code: $EXIT_CODE"
    echo "════════════════════════════════════════════════════════"
    echo ""
    echo "Press any key to close this window..."
    read -n 1
fi
EOFSCRIPT

chmod +x "$TEMP_SCRIPT"

/usr/bin/osascript <<EOFAPPLE
tell application "Terminal"
    activate
    set launchCommand to quoted form of "$TEMP_SCRIPT" & " " & quoted form of "$APP_BIN" & "; rm -f " & quoted form of "$TEMP_SCRIPT"
    do script launchCommand
    tell front window
        tell application "Finder"
            set screenBounds to bounds of window of desktop
        end tell
        set windowWidth to 700
        set windowHeight to 650
        set screenWidth to item 3 of screenBounds
        set screenHeight to item 4 of screenBounds
        set leftPos to (screenWidth - windowWidth) / 2
        set topPos to (screenHeight - windowHeight) / 2
        set rightPos to leftPos + windowWidth
        set bottomPos to topPos + windowHeight
        set bounds to {leftPos, topPos, rightPos, bottomPos}
    end tell
end tell
EOFAPPLE
EOFLAUNCH

    chmod +x "$launcher_path"
    if [[ -n "$runner_binary" ]]; then
        true
    fi
}

write_info_plist() {
    local plist_path="$1"
    local icon_key="$2"

    cat > "$plist_path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>${BUNDLE_ID}</string>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleVersion</key>
    <string>${APP_VERSION}</string>
    <key>CFBundleShortVersionString</key>
    <string>${APP_VERSION}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
${icon_key}
    <key>LSMinimumSystemVersion</key>
    <string>${MIN_MACOS}</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>

    <key>NSMicrophoneUsageDescription</key>
    <string>MixSplitR needs microphone access to record system audio for mix archiving.</string>
    <key>NSDesktopFolderUsageDescription</key>
    <string>MixSplitR needs access to save and organize your music files.</string>
    <key>NSDocumentsFolderUsageDescription</key>
    <string>MixSplitR needs access to save and organize your music files.</string>
    <key>NSDownloadsFolderUsageDescription</key>
    <string>MixSplitR needs access to process audio files from your downloads.</string>
    <key>NSRemovableVolumesUsageDescription</key>
    <string>MixSplitR needs access to process audio files from external drives.</string>
</dict>
</plist>
EOF
}

create_dmg_readme() {
    local path="$1"

    if [[ "$BUILD_MODE" == "gui" ]]; then
        cat > "$path" <<EOFREADME
${APP_NAME} v${APP_VERSION} - Installation Instructions
=======================================================

INSTALLATION:
1. Drag ${APP_NAME}.app to the Applications folder.
2. Right-click ${APP_NAME}.app and select "Open".
3. Click "Open" in the security dialog.

NOTES:
- On first launch, macOS may request microphone and file access permissions.
- Grant these permissions for recording and file processing features.

Enjoy splitting your mixes!
EOFREADME
    else
        cat > "$path" <<EOFREADME
${APP_NAME} v${APP_VERSION} - Installation Instructions
=======================================================

INSTALLATION:
1. Drag ${APP_NAME}.app to the Applications folder.
2. Right-click ${APP_NAME}.app and select "Open".
3. Click "Open" in the security dialog.

FIRST LAUNCH:
${APP_NAME} opens a Terminal window by design. This is expected behavior
for the terminal build.

NOTES:
- macOS may request microphone and file access permissions.
- Grant these permissions for full recording and file processing features.

Enjoy splitting your mixes!
EOFREADME
    fi
}

codesign_app() {
    if [[ "$SKIP_SIGN" == true ]]; then
        echo "⏭️  Skipping code signing (--skip-sign)"
        return
    fi

    if ! command -v codesign >/dev/null 2>&1; then
        echo "   ⚠️  codesign not available; skipping signing"
        return
    fi

    local entitlements_file="entitlements.plist"
    local cert

    cert="$(security find-identity -v -p codesigning 2>/dev/null | awk -F '"' '/Developer ID Application/ {print $2; exit}')"

    echo "🔐 Code signing..."
    if [[ -n "$cert" ]]; then
        echo "   Found certificate: $cert"
        if [[ -f "$entitlements_file" ]]; then
            if ! codesign --force --deep --options runtime --timestamp --entitlements "$entitlements_file" --sign "$cert" "$APP_BUNDLE"; then
                echo "   ⚠️  Developer ID signing failed. Falling back to ad-hoc signature."
                codesign --force --deep --sign - --entitlements "$entitlements_file" "$APP_BUNDLE"
            fi
        else
            if ! codesign --force --deep --options runtime --timestamp --sign "$cert" "$APP_BUNDLE"; then
                echo "   ⚠️  Developer ID signing failed. Falling back to ad-hoc signature."
                codesign --force --deep --sign - "$APP_BUNDLE"
            fi
        fi
    else
        echo "   No Developer ID certificate found. Using ad-hoc signature (local testing)."
        if [[ -f "$entitlements_file" ]]; then
            codesign --force --deep --sign - --entitlements "$entitlements_file" "$APP_BUNDLE"
        else
            codesign --force --deep --sign - "$APP_BUNDLE"
        fi
    fi
}

echo "════════════════════════════════════════════════════════"
echo "  ${APP_NAME} v${APP_VERSION} - macOS Build"
echo "════════════════════════════════════════════════════════"
echo ""
echo "Build mode: $BUILD_MODE (detected from $SPEC_FILE)"
echo "Target architecture: $TARGET_ARCH"
if [[ -n "$REQUIREMENTS_FILE" ]]; then
    echo "Dependencies source: $REQUIREMENTS_FILE"
else
    echo "Dependencies source: architecture-aligned fallback list"
fi
if [[ -f "$ARCH_DOC" ]]; then
    echo "Architecture reference: $ARCH_DOC"
fi
echo ""

if [[ "$SKIP_DEPS" == false ]]; then
    echo "📦 Installing dependencies..."
    install_brew_packages
    install_python_packages
    echo ""
else
    echo "⏭️  Skipping dependency installation (--skip-deps)"
    echo ""
fi

echo "🔍 Verifying prerequisites..."
verify_prerequisites
echo "   Python executable: $(command -v python3)"
echo "   Python version: $(python3 -V 2>/dev/null || echo 'unknown')"
echo "   Python binary: $(file "$(command -v python3)" 2>/dev/null || echo 'unknown')"
echo "   PyInstaller version: $(python3 -m PyInstaller --version 2>/dev/null || echo 'unknown')"
if [[ "$TARGET_ARCH" == "universal2" ]]; then
    if ! file "$(command -v python3)" 2>/dev/null | grep -q "universal binary"; then
        echo "   ⚠️  python3 is not a universal binary; universal2 builds may fail."
    fi
fi

echo ""
if python_has_module "PIL"; then
    HAS_PILLOW=true
    echo "🎨 Pillow is installed"
else
    HAS_PILLOW=false
    echo "🎨 Pillow not found - icon/background generation disabled"
fi

if [[ "$HAS_PILLOW" == true && ! -f "icon.icns" ]]; then
    echo "🎨 Generating icon.icns..."
    generate_icon_icns_inline "icon.icns" "" || echo "   ⚠️  Icon generation failed"
fi

if [[ ! -f "dmg_icon.icns" && -f "icon.icns" ]]; then
    cp "icon.icns" "dmg_icon.icns"
fi

if [[ "$HAS_PILLOW" == true && ! -f "background.png" ]]; then
    echo "🎨 Generating DMG background..."
    generate_dmg_background_inline "background.png" "$APP_NAME" "$APP_VERSION" || echo "   ⚠️  DMG background generation failed"
fi

if [[ ! -f "dmg_icon.icns" && -f "icon.icns" ]]; then
    cp "icon.icns" "dmg_icon.icns"
fi

HAS_BACKGROUND=false
HAS_DMG_ICON=false
USE_FANCY_DMG=false

if [[ -f "background.png" ]]; then
    HAS_BACKGROUND=true
    echo "✅ DMG background ready"
fi

if [[ -f "dmg_icon.icns" ]]; then
    HAS_DMG_ICON=true
    echo "✅ DMG icon ready"
fi

if command -v create-dmg >/dev/null 2>&1; then
    USE_FANCY_DMG=true
else
    echo "⚠️  create-dmg not found; using hdiutil fallback"
fi

echo ""
echo "🧹 Cleaning previous builds..."
rm -rf build dist "$APP_BUNDLE" dmg_staging
rm -f "${APP_NAME}-v"*.dmg rw.*.dmg || true
# Purge any stale Python bytecache in the source directory so PyInstaller
# always recompiles from source. This prevents a scenario where the ARM and
# x86 builds use different Python versions (e.g. 3.11 vs 3.14) and the wrong
# architecture's cached .pyc is silently bundled instead of the current source.
find "${SCRIPT_DIR}" -maxdepth 3 -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "${SCRIPT_DIR}" -maxdepth 3 -name "*.pyc" -delete 2>/dev/null || true
# Also fully wipe the shared PyInstaller bytecache directory so ARM-compiled
# .pyc entries are never reused by the x86 build (both share the same dir).
export PYINSTALLER_CONFIG_DIR="${SCRIPT_DIR}/.pyinstaller-cache"
rm -rf "${PYINSTALLER_CONFIG_DIR}"
mkdir -p "${PYINSTALLER_CONFIG_DIR}"
PYI_WORK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mixsplitr_pyi_work.XXXXXX")"
PYI_WORKDIR="${PYI_WORK_ROOT}/work"
mkdir -p "${PYI_WORKDIR}"
echo "✅ Cleaned"
echo ""

echo "🔨 Building with PyInstaller..."
echo "   Using PyInstaller config dir: ${PYINSTALLER_CONFIG_DIR}"
echo "   Using PyInstaller work dir: ${PYI_WORKDIR}"
PYINSTALLER_ARGS=(--clean --workpath "${PYI_WORKDIR}")
if [[ "$TARGET_ARCH" != "auto" ]]; then
    export MIXSPLITR_TARGET_ARCH="$TARGET_ARCH"
    echo "   Using target arch from env: MIXSPLITR_TARGET_ARCH=${MIXSPLITR_TARGET_ARCH}"
else
    unset MIXSPLITR_TARGET_ARCH || true
fi
python3 -m PyInstaller "${PYINSTALLER_ARGS[@]}" "$SPEC_FILE"

APP_BINARY="dist/${APP_NAME}"
if [[ ! -f "$APP_BINARY" ]]; then
    APP_BINARY="$(find dist -maxdepth 1 -type f -perm -111 | head -1 || true)"
fi

if [[ -z "$APP_BINARY" || ! -f "$APP_BINARY" ]]; then
    echo "❌ Build failed: no executable found in dist/"
    exit 1
fi

echo "✅ PyInstaller build complete"
echo "   Binary: $APP_BINARY"
echo ""

echo "📦 Creating .app bundle..."
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

if [[ "$BUILD_MODE" == "gui" ]]; then
    cp "$APP_BINARY" "$APP_BUNDLE/Contents/MacOS/${APP_NAME}"
    chmod +x "$APP_BUNDLE/Contents/MacOS/${APP_NAME}"
else
    cp "$APP_BINARY" "$APP_BUNDLE/Contents/Resources/${APP_NAME}"
    chmod +x "$APP_BUNDLE/Contents/Resources/${APP_NAME}"
    create_terminal_launcher "$APP_BUNDLE/Contents/MacOS/${APP_NAME}" "$APP_BUNDLE/Contents/Resources/${APP_NAME}"
fi

ICON_KEY=''
if [[ -f "icon.icns" ]]; then
    cp "icon.icns" "$APP_BUNDLE/Contents/Resources/icon.icns"
    ICON_KEY='    <key>CFBundleIconFile</key>
    <string>icon.icns</string>'
fi

write_info_plist "$APP_BUNDLE/Contents/Info.plist" "$ICON_KEY"

echo "✅ .app bundle created"
echo ""

codesign_app

echo ""
echo "📦 Creating DMG..."
mkdir -p dmg_staging
cp -r "$APP_BUNDLE" dmg_staging/
create_dmg_readme "dmg_staging/README.txt"

if [[ "$HAS_BACKGROUND" == true ]]; then
    mkdir -p dmg_staging/.background
    cp background.png dmg_staging/.background/background.png
fi

if [[ "$USE_FANCY_DMG" == true ]]; then
    echo "   Using create-dmg..."
    CREATE_DMG_ARGS=(
        --volname "${APP_NAME} v${APP_VERSION}"
        --window-pos 200 120
        --window-size 600 450
        --icon-size 100
        --text-size 12
        --icon "${APP_BUNDLE}" 175 160
        --hide-extension "${APP_BUNDLE}"
        --app-drop-link 425 160
        --no-internet-enable
    )

    if [[ "$HAS_BACKGROUND" == true ]]; then
        CREATE_DMG_ARGS+=(--background "background.png")
    fi

    if [[ "$HAS_DMG_ICON" == true ]]; then
        CREATE_DMG_ARGS+=(--volicon "dmg_icon.icns")
    fi

    rm -f rw.*.dmg || true

    if ! create-dmg "${CREATE_DMG_ARGS[@]}" "$DMG_NAME" "dmg_staging/"; then
        echo "   ⚠️  create-dmg failed, using hdiutil fallback"
        rm -f rw.*.dmg || true
        create_basic_dmg_with_icon "dmg_staging" "${APP_NAME} v${APP_VERSION}" "$DMG_NAME" "dmg_icon.icns"
    fi

    rm -f rw.*.dmg || true
else
    echo "   Using hdiutil fallback..."
    create_basic_dmg_with_icon "dmg_staging" "${APP_NAME} v${APP_VERSION}" "$DMG_NAME" "dmg_icon.icns"
fi

apply_dmg_file_icon "$DMG_NAME" "dmg_icon.icns"

rm -rf dmg_staging
rm -f rw.*.dmg || true

if [[ ! -f "$DMG_NAME" ]]; then
    echo "❌ DMG creation failed"
    exit 1
fi

echo ""
echo "════════════════════════════════════════════════════════"
echo "  ✅ Build Complete"
echo "════════════════════════════════════════════════════════"
echo ""
echo "Outputs:"
echo "  • ${APP_BUNDLE}"
echo "  • ${DMG_NAME}"
echo ""

if [[ -d "$APP_BUNDLE" ]]; then
    echo "App size: $(du -sh "$APP_BUNDLE" | cut -f1)"
fi

if [[ -f "$DMG_NAME" ]]; then
    echo "DMG size: $(du -sh "$DMG_NAME" | cut -f1)"
fi

echo ""
echo "Test commands:"
echo "  open ${APP_BUNDLE}"
echo "  open ${DMG_NAME}"
