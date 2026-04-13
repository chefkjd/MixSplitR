#!/bin/bash
# Build both macOS architectures and store labeled outputs.
#
# Default expected virtual environments:
#   arm64/universal host env: .venv-u2
#   x86_64 Rosetta env:       .venv-x64

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

APP_NAME="MixSplitR"
BUILD_SCRIPT="$SCRIPT_DIR/build_mac_dmg_TERMINAL.sh"
SPEC_FILE="$SCRIPT_DIR/MixSplitR_ONEFILE.spec"
ARM64_VENV=".venv-u2"
X64_VENV=".venv-x64"
SKIP_SIGN=true
SKIP_DEPS=true
BUILD_FLAGS_DIR="$SCRIPT_DIR/.build-flags"
X64_ESSENTIA_DISABLE_FLAG="$BUILD_FLAGS_DIR/x86_disable_essentia"
REQUIRE_X64_ESSENTIA=false

resolve_venv_path() {
    local configured_path="$1"
    local local_candidate parent_candidate

    case "$configured_path" in
        /*)
            echo "$configured_path"
            return
            ;;
    esac

    local_candidate="$PROJECT_ROOT/$configured_path"
    parent_candidate="$SCRIPT_DIR/../$configured_path"

    if [[ -x "$local_candidate/bin/python3" ]]; then
        echo "$local_candidate"
        return
    fi

    if [[ -x "$parent_candidate/bin/python3" ]]; then
        echo "$parent_candidate"
        return
    fi

    echo "$local_candidate"
}

usage() {
    cat <<'USAGE'
Usage: ./build_mac_dual_arch.sh [options]

Options:
  --arm64-venv <path>   Arm64 build venv (default: .venv-u2)
  --x64-venv <path>     x86_64 build venv (default: .venv-x64)
  --spec <file>         Spec file (default: MixSplitR_ONEFILE.spec)
  --with-sign           Enable code signing in sub-builds
  --with-deps           Install dependencies in sub-builds
  --require-x64-essentia  Fail build if x86_64 Essentia fallback would be used
  -h, --help            Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
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
        --spec)
            [[ $# -lt 2 ]] && { echo "❌ --spec requires a file path"; exit 1; }
            SPEC_FILE="$2"
            shift 2
            ;;
        --with-sign)
            SKIP_SIGN=false
            shift
            ;;
        --with-deps)
            SKIP_DEPS=false
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

if [[ "$SPEC_FILE" != /* ]]; then
    if [[ -f "$SCRIPT_DIR/$SPEC_FILE" ]]; then
        SPEC_FILE="$SCRIPT_DIR/$SPEC_FILE"
    elif [[ -f "$PROJECT_ROOT/$SPEC_FILE" ]]; then
        SPEC_FILE="$PROJECT_ROOT/$SPEC_FILE"
    fi
fi

if [[ ! -x "$BUILD_SCRIPT" ]]; then
    echo "❌ Build script not found or not executable: $BUILD_SCRIPT"
    exit 1
fi

if [[ ! -f "$SPEC_FILE" ]]; then
    echo "❌ Spec file not found: $SPEC_FILE"
    exit 1
fi

ARM64_VENV="$(resolve_venv_path "$ARM64_VENV")"
X64_VENV="$(resolve_venv_path "$X64_VENV")"

resolve_version() {
    local core_file="$PROJECT_ROOT/mixsplitr_core.py"
    if [[ -f "$core_file" ]]; then
        local version
        version="$(grep -E "CURRENT_VERSION\s*=\s*['\"][^'\"]+['\"]" "$core_file" | head -1 | sed -E "s/.*['\"]([^'\"]+)['\"].*/\1/" || true)"
        if [[ -n "$version" ]]; then
            echo "$version"
            return
        fi
    fi
    echo "8.0"
}

APP_VERSION="$(resolve_version)"
RELEASE_DIR="$PROJECT_ROOT/release_artifacts"
mkdir -p "$RELEASE_DIR"

if [[ ! -x "$ARM64_VENV/bin/python3" ]]; then
    echo "❌ Missing arm64 venv python: $ARM64_VENV/bin/python3"
    echo "   Run ./setup_mac_dual_venvs.sh first, or pass --arm64-venv <path>"
    exit 1
fi

if [[ ! -x "$X64_VENV/bin/python3" ]]; then
    echo "❌ Missing x86_64 venv python: $X64_VENV/bin/python3"
    echo "   Run ./setup_mac_dual_venvs.sh first, or pass --x64-venv <path>"
    echo "   Create it with:"
    echo "   arch -x86_64 /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m venv .venv-x64"
    exit 1
fi

if ! arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
    echo "❌ Rosetta is required for x86_64 build."
    echo "   Install with: softwareupdate --install-rosetta --agree-to-license"
    exit 1
fi

probe_x64_essentia_import() {
    local venv_python="$1"
    python3 - "$venv_python" <<'PY'
import subprocess
import sys

venv_python = sys.argv[1]
cmd = ["arch", "-x86_64", venv_python, "-c", "import essentia, essentia.standard"]

try:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20.0,
        check=False,
    )
except subprocess.TimeoutExpired:
    raise SystemExit(124)
except Exception:
    raise SystemExit(125)

raise SystemExit(proc.returncode)
PY
}

app_matches_arch() {
    local target_arch="$1"
    local app_bin="$PROJECT_ROOT/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"
    if [[ ! -f "$app_bin" ]]; then
        return 1
    fi

    local info
    info="$(file "$app_bin" 2>/dev/null || true)"

    case "$target_arch" in
        arm64)
            [[ "$info" == *"arm64"* ]]
            ;;
        x86_64)
            [[ "$info" == *"x86_64"* ]]
            ;;
        universal2)
            [[ "$info" == *"arm64"* && "$info" == *"x86_64"* ]]
            ;;
        *)
            return 1
            ;;
    esac
}

run_arch_build() {
    local target_arch="$1"
    local venv_path="$2"
    local build_failed=false
    local app_src="$PROJECT_ROOT/${APP_NAME}.app"
    local dmg_src="$PROJECT_ROOT/${APP_NAME}-v${APP_VERSION}.dmg"

    if [[ ! -x "$venv_path/bin/python3" ]]; then
        echo "❌ Missing venv python for ${target_arch}: $venv_path/bin/python3"
        return 1
    fi

    # Prevent stale outputs from previous arch runs being mislabeled.
    rm -rf "$app_src"
    rm -f "$dmg_src"

    local cmd=("$BUILD_SCRIPT" "--target-arch" "$target_arch" "--spec" "$SPEC_FILE")
    if [[ "$SKIP_DEPS" == true ]]; then
        cmd+=("--skip-deps")
    fi
    if [[ "$SKIP_SIGN" == true ]]; then
        cmd+=("--skip-sign")
    fi

    local quoted_cmd=""
    local arg
    for arg in "${cmd[@]}"; do
        quoted_cmd+=" $(printf '%q' "$arg")"
    done

    local disable_essentia=false
    if [[ "$target_arch" == "x86_64" ]]; then
        if [[ -f "$X64_ESSENTIA_DISABLE_FLAG" ]]; then
            disable_essentia=true
            if [[ "$REQUIRE_X64_ESSENTIA" == true ]]; then
                echo "❌ x86_64 Essentia is required, but fallback flag is set:"
                echo "   $X64_ESSENTIA_DISABLE_FLAG"
                echo "   Re-run setup with x86 SDL2 available (see setup output guidance)."
                return 1
            fi
        elif ! probe_x64_essentia_import "$venv_path/bin/python3"; then
            if [[ "$REQUIRE_X64_ESSENTIA" == true ]]; then
                echo "❌ x86_64 Essentia is required, but import probe failed."
                echo "   Re-run setup after installing x86_64/universal SDL2."
                return 1
            else
                disable_essentia=true
                mkdir -p "$BUILD_FLAGS_DIR"
                echo "1" > "$X64_ESSENTIA_DISABLE_FLAG"
                echo "⚠️  x86_64 essentia import probe failed; enabling x86 fallback flag."
            fi
        fi
    fi

    local essentia_flag_cmd="export MIXSPLITR_DISABLE_ESSENTIA=0"
    if [[ "$disable_essentia" == true ]]; then
        essentia_flag_cmd="export MIXSPLITR_DISABLE_ESSENTIA=1"
    fi

    local shell_script="cd $(printf '%q' "$PROJECT_ROOT") && source $(printf '%q' "$venv_path/bin/activate") && $essentia_flag_cmd &&${quoted_cmd}"

    echo ""
    echo "════════════════════════════════════════"
    echo "Building ${target_arch}"
    echo "Venv: $venv_path"
    if [[ "$target_arch" == "x86_64" && "$disable_essentia" == true ]]; then
        echo "Essentia bundling: DISABLED (flag: $X64_ESSENTIA_DISABLE_FLAG)"
    fi
    echo "════════════════════════════════════════"

    if [[ "$target_arch" == "x86_64" ]]; then
        if ! arch -x86_64 /bin/zsh -lc "$shell_script"; then
            build_failed=true
        fi
    else
        if ! /bin/zsh -lc "$shell_script"; then
            build_failed=true
        fi
    fi

    if [[ "$build_failed" == true ]]; then
        if [[ -d "$PROJECT_ROOT/${APP_NAME}.app" ]] && app_matches_arch "$target_arch"; then
            echo "⚠️  ${target_arch} build command returned non-zero, but ${APP_NAME}.app exists."
            echo "   Continuing and collecting artifacts from the built app bundle."
        else
            echo "❌ ${target_arch} build failed before a valid ${target_arch} app bundle was produced."
            return 1
        fi
    fi

    if ! app_matches_arch "$target_arch"; then
        local app_bin="$PROJECT_ROOT/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"
        local info
        info="$(file "$app_bin" 2>/dev/null || echo 'unknown')"
        echo "❌ Built app architecture does not match target '${target_arch}'."
        echo "   Detected: $info"
        return 1
    fi
}

collect_outputs() {
    local target_arch="$1"
    local app_src="$PROJECT_ROOT/${APP_NAME}.app"
    local dmg_src="$PROJECT_ROOT/${APP_NAME}-v${APP_VERSION}.dmg"
    local app_dst="$RELEASE_DIR/${APP_NAME}-${target_arch}.app"
    local zip_dst="$RELEASE_DIR/${APP_NAME}-v${APP_VERSION}-${target_arch}.zip"
    local dmg_dst="$RELEASE_DIR/${APP_NAME}-v${APP_VERSION}-${target_arch}.dmg"

    if [[ ! -d "$app_src" ]]; then
        echo "❌ Expected app bundle not found after ${target_arch} build: $app_src"
        return 1
    fi

    if ! app_matches_arch "$target_arch"; then
        local app_bin="$app_src/Contents/MacOS/${APP_NAME}"
        local info
        info="$(file "$app_bin" 2>/dev/null || echo 'unknown')"
        echo "❌ Refusing to collect ${target_arch} artifacts from non-matching app binary."
        echo "   Detected: $info"
        return 1
    fi

    rm -rf "$app_dst"
    cp -R "$app_src" "$app_dst"

    rm -f "$zip_dst"
    ditto -c -k --sequesterRsrc --keepParent "$app_src" "$zip_dst"

    if [[ -f "$dmg_src" ]]; then
        cp -f "$dmg_src" "$dmg_dst"
    else
        echo "⚠️  DMG not found for ${target_arch}; created ZIP artifact instead."
    fi

    echo "Saved artifacts for ${target_arch}:"
    echo "  - $app_dst"
    echo "  - $zip_dst"
    if [[ -f "$dmg_dst" ]]; then
        echo "  - $dmg_dst"
    fi
}

echo "════════════════════════════════════════════════════════"
echo "  ${APP_NAME} v${APP_VERSION} Dual-Arch Build"
echo "════════════════════════════════════════════════════════"
echo "Spec: $SPEC_FILE"
echo "Arm64 venv: $ARM64_VENV"
echo "x86_64 venv: $X64_VENV"
echo "Release output dir: $RELEASE_DIR"
echo "Require x86_64 Essentia: $REQUIRE_X64_ESSENTIA"
if [[ -f "$X64_ESSENTIA_DISABLE_FLAG" ]]; then
    echo "x86_64 essentia fallback flag: ENABLED"
fi

run_arch_build "arm64" "$ARM64_VENV"
collect_outputs "arm64"

run_arch_build "x86_64" "$X64_VENV"
collect_outputs "x86_64"

echo ""
echo "✅ Dual-arch build flow complete."
echo "Artifacts:"
ls -la "$RELEASE_DIR"
