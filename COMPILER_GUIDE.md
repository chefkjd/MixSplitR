# MixSplitR Compiler Guide v6.9

This guide covers how to compile MixSplitR into standalone executables for Mac and Windows.

---

## 📦 What You Need

### Mac Requirements
- Python 3.6+
- Homebrew (recommended for installing dependencies)
- ffmpeg/ffprobe (install via Homebrew)
- fpcalc/chromaprint (optional, for MusicBrainz fallback)

### Windows Requirements
- Python 3.6+
- ffmpeg.exe and ffprobe.exe (must be in same folder)
- fpcalc.exe (optional, for MusicBrainz fallback)
- icon.ico (optional, for custom executable icon)

---

## 🍎 Mac Compilation

### Quick Start
```bash
# Make the compiler executable
chmod +x compile_mac.sh

# Run it
./compile_mac.sh
```

### What the Compiler Does
1. Installs required Python packages
2. Auto-detects ffmpeg/ffprobe from Homebrew
3. Copies binaries to working directory
4. Checks for fpcalc (optional)
5. Builds executable with PyInstaller

### Prerequisites
```bash
# Install Homebrew (if not installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install ffmpeg (includes ffprobe)
brew install ffmpeg

# Install chromaprint (optional, for MusicBrainz fallback)
brew install chromaprint
```

### Output
- **Executable**: `dist/MixSplitR`
- **Run with**: `./dist/MixSplitR` or double-click in Finder

---

## 🪟 Windows Compilation

### Compiler Options

| Script | Use Case |
|--------|----------|
| `compile_windows.bat` | Full build with all checks (recommended) |
| `compile_windows_clean.bat` | Fresh build - cleans old files first |
| `compile_windows_simple.bat` | Minimal build, fewer checks |

### Option 1: Full Compiler (Recommended)
```batch
compile_windows.bat
```

**Features:**
- Checks for all required binaries
- Detects icon.ico for custom executable icon
- Detects fpcalc.exe for MusicBrainz support
- Full error handling and prompts

### Option 2: Clean Build
```batch
compile_windows_clean.bat
```

**Features:**
- Deletes old build/dist folders first
- Fresh compilation from scratch
- Same checks as full compiler
- Use when troubleshooting build issues

### Option 3: Simple Compiler
```batch
compile_windows_simple.bat
```

**Features:**
- Minimal error checking
- Fastest option
- Assumes ffmpeg.exe/ffprobe.exe are present
- No MusicBrainz support

### Windows Prerequisites

#### 1. Download ffmpeg (Required)
- Visit: https://github.com/BtbN/FFmpeg-Builds/releases
- Download: `ffmpeg-master-latest-win64-gpl.zip`
- Extract and copy `ffmpeg.exe` and `ffprobe.exe` to your MixSplitR folder

#### 2. Download fpcalc (Optional - for MusicBrainz)
- Visit: https://acoustid.org/chromaprint
- Download Windows binary
- Copy `fpcalc.exe` to your MixSplitR folder

#### 3. Add Custom Icon (Optional)
- Create or obtain an `.ico` file
- Name it `icon.ico` and place in your MixSplitR folder
- The compiler will automatically detect and use it

### Output
- **Executable**: `dist\MixSplitR.exe`
- **Run with**: `dist\MixSplitR.exe` or double-click

---

## 📁 File Structure Before Compiling

### Mac
```
MixSplitR/
├── MixSplitR.py
├── compile_mac.sh
└── (ffmpeg/ffprobe auto-copied from Homebrew)
```

### Windows
```
MixSplitR/
├── MixSplitR.py
├── compile_windows.bat
├── compile_windows_clean.bat
├── compile_windows_simple.bat
├── ffmpeg.exe          ← Required
├── ffprobe.exe         ← Required
├── fpcalc.exe          ← Optional (MusicBrainz)
└── icon.ico            ← Optional (custom icon)
```

---

## 🔧 Troubleshooting

### Mac Issues

**"ffmpeg not found"**
```bash
brew install ffmpeg
```

**"Permission denied"**
```bash
chmod +x compile_mac.sh
```

**"fpcalc not found" but you want MusicBrainz**
```bash
brew install chromaprint
```

**Build fails with module errors**
```bash
pip install --upgrade pydub mutagen acrcloud requests tqdm psutil pyacoustid musicbrainzngs pyinstaller
```

### Windows Issues

**"ffmpeg.exe not found"**
- Download from: https://github.com/BtbN/FFmpeg-Builds/releases
- Place in same folder as MixSplitR.py

**"Python not recognized"**
- Ensure Python is added to PATH during installation
- Or use `py -m PyInstaller` instead of `python -m PyInstaller`

**Build fails with "module not found"**
```batch
pip install --upgrade pydub mutagen acrcloud requests tqdm psutil pyacoustid musicbrainzngs pyinstaller
```
Then use `compile_windows_clean.bat` for a fresh build.

**Executable crashes on startup**
- Verify all binaries (ffmpeg.exe, ffprobe.exe) were in folder during compilation
- Try clean build with `compile_windows_clean.bat`
- Check that Python packages installed correctly

**Icon not appearing**
- Ensure file is named exactly `icon.ico`
- Verify it's a valid Windows icon file (.ico format)
- Rebuild with `compile_windows_clean.bat`

---

## ✅ Testing Your Executable

### Mac
```bash
cd dist
./MixSplitR
```

### Windows
```batch
cd dist
MixSplitR.exe
```

You should see the MixSplitR startup menu with 4 options.

---

## 📊 Expected File Sizes

| Configuration | Mac | Windows |
|--------------|-----|---------|
| With MusicBrainz (fpcalc) | ~80-100 MB | ~90-110 MB |
| Without MusicBrainz | ~75-95 MB | ~85-105 MB |

---

## 🚀 Distribution

Once compiled, you can distribute just the executable:
- **Mac**: Copy `dist/MixSplitR` to any Mac
- **Windows**: Copy `dist/MixSplitR.exe` to any Windows PC

The executable includes everything needed:
- Python runtime
- All libraries (pydub, mutagen, etc.)
- ffmpeg and ffprobe binaries
- fpcalc (if included)

**Note:** Users will need to create a `config.json` with their ACRCloud API keys on first run. The program prompts for this automatically.

---

## 🔄 Updating to New Versions

1. Replace `MixSplitR.py` with the new version
2. **Windows**: Use `compile_windows_clean.bat` for clean build
3. **Mac**: Just run `./compile_mac.sh` (auto-cleans)

---

## 💡 Quick Reference

| Task | Mac | Windows |
|------|-----|---------|
| **Quick compile** | `./compile_mac.sh` | `compile_windows.bat` |
| **Clean build** | (auto) | `compile_windows_clean.bat` |
| **Simple build** | N/A | `compile_windows_simple.bat` |
| **Install ffmpeg** | `brew install ffmpeg` | [Download](https://github.com/BtbN/FFmpeg-Builds/releases) |
| **Install fpcalc** | `brew install chromaprint` | [Download](https://acoustid.org/chromaprint) |
| **Run executable** | `./dist/MixSplitR` | `dist\MixSplitR.exe` |

---

## 📝 Notes

- **Mac compiler** auto-detects and copies binaries from Homebrew
- **Windows compiler** requires manual placement of binaries in folder
- **fpcalc is optional** - without it, MusicBrainz fallback is disabled but ACRCloud still works
- **icon.ico is optional** - without it, Windows uses default Python icon
- **Compilation takes 2-5 minutes** depending on your system
- **First run of compiled app may be slower** as it extracts bundled files

---

## 🔗 Links

- **Main README**: See `README.md` for usage instructions
- **Releases**: https://github.com/chefkjd/MixSplitR/releases
- **ffmpeg Downloads**: https://github.com/BtbN/FFmpeg-Builds/releases
- **Chromaprint/fpcalc**: https://acoustid.org/chromaprint

---

**Questions?** Check the main README.md or visit: https://github.com/chefkjd/MixSplitR
