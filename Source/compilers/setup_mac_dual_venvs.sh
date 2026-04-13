#!/bin/bash
# Create arm64 + x86_64 venvs with the same dependency set for macOS build parity.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
ARM64_VENV=".venv-u2"
X64_VENV=".venv-x64"
ARM64_REQUIREMENTS_FILE="requirements-macos-parity.txt"
X64_REQUIREMENTS_FILE="requirements-macos-x64-essentia.txt"
WITH_OPTIONAL_FEATURES=false
BUILD_FLAGS_DIR="$SCRIPT_DIR/.build-flags"
X64_ESSENTIA_DISABLE_FLAG="$BUILD_FLAGS_DIR/x86_disable_essentia"
X64_DISABLE_ESSENTIA=false
REQUIRE_X64_ESSENTIA=false
X64_SDL2_DYLIB=""

probe_x64_import_expr() {
    local expr="$1"
    local timeout_seconds="${2:-20}"
    python3 - "$X64_VENV/bin/python3" "$expr" "$timeout_seconds" <<'PY'
import subprocess
import sys

venv_python = sys.argv[1]
expr = sys.argv[2]
timeout_seconds = float(sys.argv[3])
cmd = ["arch", "-x86_64", venv_python, "-c", expr]

try:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=max(1.0, timeout_seconds),
        check=False,
    )
except subprocess.TimeoutExpired:
    raise SystemExit(124)
except Exception:
    raise SystemExit(125)

raise SystemExit(proc.returncode)
PY
}

_dylib_has_x86_64_arch() {
    local dylib_path="$1"
    lipo -archs "$dylib_path" 2>/dev/null | tr ' ' '\n' | grep -qx 'x86_64'
}

print_x64_sdl2_guidance() {
    echo "   [optional]    To enable x86_64 Essentia parity, provide an x86_64/universal SDL2 dylib."
    if [[ -x "/usr/local/bin/brew" ]]; then
        echo "   [optional]    Detected Intel Homebrew. Install with:"
        echo "   [optional]      arch -x86_64 /usr/local/bin/brew install sdl2"
    else
        echo "   [optional]    Intel Homebrew not found at /usr/local/bin/brew."
        echo "   [optional]    Install Rosetta Homebrew in /usr/local, then:"
        echo "   [optional]      arch -x86_64 /usr/local/bin/brew install sdl2"
    fi
    echo "   [optional]    Or re-run with:"
    echo "   [optional]      --x64-sdl2-dylib /path/to/libSDL2-2.0.0.dylib"
}

ensure_x64_sdl2_for_essentia() {
    local ess_dir=""
    local d
    for d in "$X64_VENV"/lib/python*/site-packages/essentia/.dylibs; do
        if [[ -d "$d" ]]; then
            ess_dir="$d"
            break
        fi
    done

    if [[ -z "$ess_dir" ]]; then
        return 1
    fi

    if [[ -f "$ess_dir/libSDL2-2.0.0.dylib" ]] && _dylib_has_x86_64_arch "$ess_dir/libSDL2-2.0.0.dylib"; then
        echo "   [optional] x86_64 SDL2 bridge already present in Essentia dylibs."
        return 0
    fi

    local candidates=()
    if [[ -n "$X64_SDL2_DYLIB" ]]; then
        candidates+=("$X64_SDL2_DYLIB")
    fi

    candidates+=(
        "/usr/local/lib/libSDL2-2.0.0.dylib"
        "/usr/local/lib/libSDL2-2.0.dylib"
        "/usr/local/opt/sdl2/lib/libSDL2-2.0.0.dylib"
        "/usr/local/opt/sdl2/lib/libSDL2-2.0.dylib"
        "/opt/homebrew/lib/libSDL2-2.0.0.dylib"
        "/opt/homebrew/lib/libSDL2-2.0.dylib"
        "/opt/homebrew/opt/sdl2/lib/libSDL2-2.0.0.dylib"
        "/opt/homebrew/opt/sdl2/lib/libSDL2-2.0.dylib"
    )

    local had_nullglob=0
    if shopt -q nullglob; then
        had_nullglob=1
    fi
    shopt -s nullglob
    local cellar_candidates=(
        /usr/local/Cellar/sdl2/*/lib/libSDL2-2.0.0.dylib
        /usr/local/Cellar/sdl2/*/lib/libSDL2-2.0.dylib
        /opt/homebrew/Cellar/sdl2/*/lib/libSDL2-2.0.0.dylib
        /opt/homebrew/Cellar/sdl2/*/lib/libSDL2-2.0.dylib
    )
    if [[ ${#cellar_candidates[@]} -gt 0 ]]; then
        candidates+=("${cellar_candidates[@]}")
    fi
    if [[ "$had_nullglob" -eq 0 ]]; then
        shopt -u nullglob
    fi

    local cand
    for cand in "${candidates[@]}"; do
        if [[ ! -f "$cand" ]]; then
            continue
        fi
        if _dylib_has_x86_64_arch "$cand"; then
            cp -f "$cand" "$ess_dir/libSDL2-2.0.0.dylib"
            ln -sf "libSDL2-2.0.0.dylib" "$ess_dir/libSDL2-2.0.dylib"
            echo "   [optional] ✅ Added SDL2 bridge for x86_64 Essentia: $cand"
            return 0
        fi
    done

    echo "   [optional] ⚠️  No x86_64/universal SDL2 dylib found for Essentia."
    print_x64_sdl2_guidance
    return 1
}

usage() {
    cat <<'USAGE'
Usage: ./setup_mac_dual_venvs.sh [options]

Options:
  --python-bin <path>      Universal2 python binary (default: /Library/Frameworks/Python.framework/Versions/3.12/bin/python3)
  --arm64-venv <path>      Arm64 venv path (default: .venv-u2)
  --x64-venv <path>        x86_64 venv path (default: .venv-x64)
  --requirements <path>    Requirements file for both arch builds (legacy convenience)
  --arm64-requirements <path>  Arm64 requirements file (default: requirements-macos-parity.txt)
  --x64-requirements <path>    x86_64 requirements file (default: requirements-macos-x64-essentia.txt)
  --x64-sdl2-dylib <path>      Preferred SDL2 dylib for x86_64 Essentia bridge
  --with-optional-features Attempt optional ACRCloud/Essentia installs in both venvs
  --require-x64-essentia    Fail setup if x86_64 Essentia is unavailable
  -h, --help               Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --python-bin)
            [[ $# -lt 2 ]] && { echo "❌ --python-bin requires a path"; exit 1; }
            PYTHON_BIN="$2"
            shift 2
            ;;
        --arm64-venv)
            [[ $# -lt 2 ]] && { echo "❌ --arm64-venv requires a path"; exit 1; }
            ARM64_VENV="$2"
            shift 2
            ;;
        --x64-venv)
            [[ $# -lt 2 ]] && { echo "❌ --x64-venv requires a path"; exit 1; }
            X64_VENV="$2"
            shift 2
            ;;
        --requirements)
            [[ $# -lt 2 ]] && { echo "❌ --requirements requires a path"; exit 1; }
            ARM64_REQUIREMENTS_FILE="$2"
            X64_REQUIREMENTS_FILE="$2"
            shift 2
            ;;
        --arm64-requirements)
            [[ $# -lt 2 ]] && { echo "❌ --arm64-requirements requires a path"; exit 1; }
            ARM64_REQUIREMENTS_FILE="$2"
            shift 2
            ;;
        --x64-requirements)
            [[ $# -lt 2 ]] && { echo "❌ --x64-requirements requires a path"; exit 1; }
            X64_REQUIREMENTS_FILE="$2"
            shift 2
            ;;
        --x64-sdl2-dylib)
            [[ $# -lt 2 ]] && { echo "❌ --x64-sdl2-dylib requires a path"; exit 1; }
            X64_SDL2_DYLIB="$2"
            shift 2
            ;;
        --with-optional-features)
            WITH_OPTIONAL_FEATURES=true
            shift
            ;;
        --require-x64-essentia)
            REQUIRE_X64_ESSENTIA=true
            shift
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

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "❌ Python binary not found or not executable: $PYTHON_BIN"
    exit 1
fi

if [[ ! -f "$ARM64_REQUIREMENTS_FILE" ]]; then
    echo "❌ Arm64 requirements file not found: $ARM64_REQUIREMENTS_FILE"
    exit 1
fi

if [[ ! -f "$X64_REQUIREMENTS_FILE" ]]; then
    echo "❌ x86_64 requirements file not found: $X64_REQUIREMENTS_FILE"
    exit 1
fi

if [[ "$REQUIRE_X64_ESSENTIA" == true && "$WITH_OPTIONAL_FEATURES" != true ]]; then
    echo "❌ --require-x64-essentia requires --with-optional-features."
    exit 1
fi

if [[ -n "$X64_SDL2_DYLIB" && ! -f "$X64_SDL2_DYLIB" ]]; then
    echo "❌ --x64-sdl2-dylib not found: $X64_SDL2_DYLIB"
    exit 1
fi

if ! arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
    echo "❌ Rosetta is required for x86_64 environment setup."
    echo "   Install with: softwareupdate --install-rosetta --agree-to-license"
    exit 1
fi

mkdir -p "$BUILD_FLAGS_DIR"
rm -f "$X64_ESSENTIA_DISABLE_FLAG"

PYTHON_FILE_INFO="$(file "$PYTHON_BIN" 2>/dev/null || true)"
if [[ "$PYTHON_FILE_INFO" != *"universal binary"* ]]; then
    echo "⚠️  Python binary is not universal2:"
    echo "   $PYTHON_FILE_INFO"
    echo "   Setup may fail for one architecture."
fi

echo "════════════════════════════════════════════════════════"
echo "  MixSplitR macOS dual-venv setup"
echo "════════════════════════════════════════════════════════"
echo "Python: $PYTHON_BIN"
echo "Arm64 requirements: $ARM64_REQUIREMENTS_FILE"
echo "x86_64 requirements: $X64_REQUIREMENTS_FILE"
echo "Arm64 venv: $ARM64_VENV"
echo "x86_64 venv: $X64_VENV"
echo "Optional features: $WITH_OPTIONAL_FEATURES"
echo "Require x86_64 Essentia: $REQUIRE_X64_ESSENTIA"
if [[ -n "$X64_SDL2_DYLIB" ]]; then
    echo "x86_64 SDL2 override: $X64_SDL2_DYLIB"
fi

echo ""
echo "[1/4] Creating arm64 venv..."
rm -rf "$ARM64_VENV"
"$PYTHON_BIN" -m venv "$ARM64_VENV"
source "$ARM64_VENV/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install --prefer-binary -r "$ARM64_REQUIREMENTS_FILE"

if ! python -c "import shazamio" >/dev/null 2>&1; then
    echo "   [arm64 profile] Installing shazamio/shazamio-core without dependency resolver (keep numpy<2)..."
    if python -m pip install --prefer-binary --no-deps shazamio==0.8.1 shazamio-core==1.1.2; then
        echo "   [arm64 profile] ✅ Installed shazamio==0.8.1 and shazamio-core==1.1.2 (--no-deps)"
    else
        echo "   [arm64 profile] ⚠️  Failed to install shazamio compatibility wheels"
    fi
fi

if python -c "import shazamio" >/dev/null 2>&1; then
    echo "   [arm64 profile] shazamio: OK"
else
    echo "   [arm64 profile] ⚠️  shazamio import failed; Shazam feature may be disabled on arm64."
fi

if python -c "import objc, AppKit" >/dev/null 2>&1; then
    echo "   [arm64 profile] pyobjc/AppKit: OK"
else
    echo "   [arm64 profile] Installing pyobjc/AppKit bridge (for macOS transparency)..."
    if python -m pip install --prefer-binary pyobjc-core pyobjc-framework-Cocoa >/dev/null 2>&1 \
        && python -c "import objc, AppKit" >/dev/null 2>&1; then
        echo "   [arm64 profile] ✅ pyobjc/AppKit bridge installed"
    else
        echo "   [arm64 profile] ⚠️  pyobjc/AppKit unavailable; sidebar vibrancy/transparency may degrade."
    fi
fi

python -m pip install pyinstaller pillow
if [[ "$WITH_OPTIONAL_FEATURES" == true ]]; then
    echo "   [optional] Installing ACRCloud SDK (arm64)..."
    if python -m pip install --prefer-binary acrcloud-sdk-python; then
        echo "   [optional] ✅ ACRCloud SDK installed (arm64)"
    elif python -m pip install --prefer-binary acrcloud; then
        echo "   [optional] ✅ acrcloud installed (arm64)"
    else
        echo "   [optional] ⚠️  ACRCloud install failed (arm64)"
    fi

    echo "   [optional] Installing Essentia (arm64)..."
    if python -m pip install --prefer-binary essentia; then
        echo "   [optional] ✅ Essentia installed (arm64)"
    else
        echo "   [optional] ⚠️  Essentia install failed (arm64)"
    fi

    if python - <<'PY'
def _ok(name):
    try:
        __import__(name)
        return "OK"
    except Exception as exc:
        return f"MISSING ({type(exc).__name__}: {exc})"

print("   [optional] arm64 acrcloud:", _ok("acrcloud"))
print("   [optional] arm64 essentia:", _ok("essentia"))
PY
    then
        :
    else
        echo "   [optional] ⚠️  Import probe crashed (arm64). This is non-fatal."
    fi
fi
python -m pip freeze | sort > "$SCRIPT_DIR/.freeze-arm64.txt"
deactivate

echo ""
echo "[2/4] Creating x86_64 venv (Rosetta)..."
rm -rf "$X64_VENV"
arch -x86_64 "$PYTHON_BIN" -m venv "$X64_VENV"
source "$X64_VENV/bin/activate"
arch -x86_64 python -m pip install --upgrade pip setuptools wheel
arch -x86_64 python -m pip install --prefer-binary -r "$X64_REQUIREMENTS_FILE"

if ! arch -x86_64 python -c "import shazamio" >/dev/null 2>&1; then
    echo "   [x86_64 profile] Installing shazamio/shazamio-core without dependency resolver (keep numpy<2)..."
    if arch -x86_64 python -m pip install --prefer-binary --no-deps shazamio==0.8.1 shazamio-core==1.1.2; then
        echo "   [x86_64 profile] ✅ Installed shazamio==0.8.1 and shazamio-core==1.1.2 (--no-deps)"
    else
        echo "   [x86_64 profile] ⚠️  Failed to install shazamio compatibility wheels"
    fi
fi

if arch -x86_64 python -c "import shazamio" >/dev/null 2>&1; then
    echo "   [x86_64 profile] shazamio: OK"
else
    echo "   [x86_64 profile] ⚠️  shazamio import failed; Shazam feature may be disabled on x86_64."
fi

if arch -x86_64 python -c "import objc, AppKit" >/dev/null 2>&1; then
    echo "   [x86_64 profile] pyobjc/AppKit: OK"
else
    echo "   [x86_64 profile] Installing pyobjc/AppKit bridge (for macOS transparency)..."
    if arch -x86_64 python -m pip install --prefer-binary pyobjc-core pyobjc-framework-Cocoa >/dev/null 2>&1 \
        && arch -x86_64 python -c "import objc, AppKit" >/dev/null 2>&1; then
        echo "   [x86_64 profile] ✅ pyobjc/AppKit bridge installed"
    else
        echo "   [x86_64 profile] ⚠️  pyobjc/AppKit unavailable; sidebar vibrancy/transparency may degrade."
    fi
fi

arch -x86_64 python -m pip install pyinstaller pillow
if [[ "$WITH_OPTIONAL_FEATURES" == true ]]; then
    echo "   [optional] Installing ACRCloud SDK (x86_64)..."
    if arch -x86_64 python -m pip install --prefer-binary acrcloud-sdk-python; then
        echo "   [optional] ✅ ACRCloud SDK installed (x86_64)"
    elif arch -x86_64 python -m pip install --prefer-binary acrcloud; then
        echo "   [optional] ✅ acrcloud installed (x86_64)"
    else
        echo "   [optional] ⚠️  ACRCloud install failed (x86_64)"
    fi

    echo "   [optional] Installing Essentia (x86_64)..."
    x64_sdl2_ok=false
    if arch -x86_64 python -m pip install --prefer-binary essentia; then
        echo "   [optional] ✅ Essentia installed (x86_64)"
        if ensure_x64_sdl2_for_essentia; then
            x64_sdl2_ok=true
        fi
    else
        echo "   [optional] ⚠️  Essentia install failed (x86_64)"
    fi

    if probe_x64_import_expr "import acrcloud"; then
        echo "   [optional] x86_64 acrcloud: OK"
    else
        echo "   [optional] x86_64 acrcloud: MISSING/FAILED"
    fi

    if probe_x64_import_expr "import essentia, essentia.standard"; then
        echo "   [optional] x86_64 essentia: OK"
        echo "   [optional] x86_64 essentia.standard: OK"
    else
        echo "   [optional] ⚠️  x86_64 essentia import probe failed or crashed."
        if [[ "$REQUIRE_X64_ESSENTIA" == true ]]; then
            echo "❌ x86_64 Essentia is required, but import probe failed."
            if [[ "$x64_sdl2_ok" != true ]]; then
                print_x64_sdl2_guidance
            fi
            exit 1
        else
            echo "   [optional]    Essentia will be disabled for x86_64 packaging."
            X64_DISABLE_ESSENTIA=true
        fi
    fi
fi
if [[ "$WITH_OPTIONAL_FEATURES" != true ]]; then
    echo "   [optional] Optional feature installs were skipped."
    echo "   [optional] x86_64 Essentia will remain disabled for packaging."
    X64_DISABLE_ESSENTIA=true
fi
arch -x86_64 python -m pip freeze | sort > "$SCRIPT_DIR/.freeze-x64.txt"
deactivate

if [[ "$X64_DISABLE_ESSENTIA" == true ]]; then
    echo "1" > "$X64_ESSENTIA_DISABLE_FLAG"
    echo "⚠️  Wrote build flag: $X64_ESSENTIA_DISABLE_FLAG"
    echo "   x86_64 builds will skip bundling essentia."
else
    rm -f "$X64_ESSENTIA_DISABLE_FLAG"
    echo "✅ x86_64 essentia runtime probe passed; full optional bundling enabled."
fi

echo ""
echo "[3/4] Comparing installed package lists..."
if diff -u "$SCRIPT_DIR/.freeze-arm64.txt" "$SCRIPT_DIR/.freeze-x64.txt" >/tmp/mixsplitr_freeze_diff.txt 2>&1; then
    echo "✅ Package lists match exactly."
else
    echo "⚠️  Package list differences detected."
    echo "   Showing first 120 lines:" 
    sed -n '1,120p' /tmp/mixsplitr_freeze_diff.txt
fi

echo ""
echo "[4/4] Quick architecture checks..."
if [[ -x "$ARM64_VENV/bin/python3" ]]; then
    file "$ARM64_VENV/bin/python3"
fi
if [[ -x "$X64_VENV/bin/python3" ]]; then
    file "$X64_VENV/bin/python3"
fi

echo ""
echo "✅ Environment setup complete."
echo "Next step:"
echo "  ./build_mac_dual_arch.sh --arm64-venv $ARM64_VENV --x64-venv $X64_VENV"
