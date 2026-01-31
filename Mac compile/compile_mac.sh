#!/bin/bash
echo "========================================"
echo "   MixSplitR Mac Compiler"
echo "========================================"
echo ""

echo "Installing required packages..."
pip3 install pydub mutagen acrcloud requests tqdm psutil pyacoustid musicbrainzngs pyinstaller

echo ""
echo "Checking for required binaries..."
if [ ! -f "ffmpeg" ]; then
    echo "[ERROR] ffmpeg not found!"
    echo "Install with: brew install ffmpeg"
    exit 1
fi
echo "[OK] Found ffmpeg"

if [ ! -f "ffprobe" ]; then
    echo "[ERROR] ffprobe not found!"
    echo "Install with: brew install ffmpeg"
    exit 1
fi
echo "[OK] Found ffprobe"

# Check for fpcalc
FPCALC_PATH=$(which fpcalc 2>/dev/null)
if [ -z "$FPCALC_PATH" ]; then
    echo "[WARNING] fpcalc not found - MusicBrainz fallback will be disabled"
    echo "Install with: brew install chromaprint"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
    BUILD_CMD="python3 -m PyInstaller --onefile \
        --hidden-import=acrcloud \
        --hidden-import=acrcloud.recognizer \
        --collect-all=acrcloud \
        --collect-all=pyacrcloud \
        --add-binary \"ffmpeg:.\" \
        --add-binary \"ffprobe:.\" \
        --name MixSplitR MixSplitR.py"
else
    echo "[OK] Found fpcalc at: $FPCALC_PATH"
    BUILD_CMD="python3 -m PyInstaller --onefile \
        --hidden-import=acrcloud \
        --hidden-import=acrcloud.recognizer \
        --collect-all=acrcloud \
        --collect-all=pyacrcloud \
        --add-binary \"ffmpeg:.\" \
        --add-binary \"ffprobe:.\" \
        --add-binary \"$FPCALC_PATH:.\" \
        --name MixSplitR MixSplitR.py"
fi

echo ""
echo "Building executable..."
eval $BUILD_CMD

echo ""
echo "========================================"
echo "Compilation complete! Check the 'dist' folder for your executable."
echo "========================================"
