@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM ================================================================
REM   MixSplitR v8.0 - Windows Build Script (GUI + CLI)
REM   Installs all dependencies and builds a Windows release package
REM
REM   Usage:
REM     compile_windows_v72.bat                         (default slimmer onedir build)
REM     compile_windows_v72.bat --onefile              (legacy single-file build)
REM     compile_windows_v72.bat --installer            (build onedir + Inno Setup installer)
REM     compile_windows_v72.bat --skip-deps --onedir   (skip pip install)
REM ================================================================

REM Handle UNC paths (network shares) - pushd maps a temp drive letter
set "COMPILERS_DIR=%~dp0"
for %%I in ("%COMPILERS_DIR%..") do set "PROJECT_ROOT=%%~fI"
pushd "%PROJECT_ROOT%"

REM Parse flags
set SKIP_DEPS=no
set BUILD_MODE=onedir
set BUILD_INSTALLER=no

:parse_args
if "%~1"=="" goto :args_done
if /I "%~1"=="--skip-deps" (
    set SKIP_DEPS=yes
    shift
    goto :parse_args
)
if /I "%~1"=="--onefile" (
    set BUILD_MODE=onefile
    shift
    goto :parse_args
)
if /I "%~1"=="--onedir" (
    set BUILD_MODE=onedir
    shift
    goto :parse_args
)
if /I "%~1"=="--installer" (
    set BUILD_INSTALLER=yes
    set BUILD_MODE=onedir
    shift
    goto :parse_args
)
echo    [WARN] Unknown option: %~1
shift
goto :parse_args

:args_done
set "SPEC_FILE=%COMPILERS_DIR%MixSplitR_ONEDIR.spec"
set OUTPUT_PATH=dist\MixSplitR\MixSplitR.exe
set INSTALLER_OUTPUT_PATH=dist\installers\MixSplitR-Setup.exe
if /I "%BUILD_MODE%"=="onefile" (
    set "SPEC_FILE=%COMPILERS_DIR%MixSplitR_ONEFILE.spec"
    set OUTPUT_PATH=dist\MixSplitR.exe
)

echo ========================================================
echo   MixSplitR v8.0 - Windows Build (GUI + CLI)
echo ========================================================
echo   Build mode: %BUILD_MODE%
echo   Installer : %BUILD_INSTALLER%
echo   Spec file : %SPEC_FILE%
echo.

REM ────────────────────────────────────────────────────────
REM STEP 1: Install Python dependencies
REM ────────────────────────────────────────────────────────

if "%SKIP_DEPS%"=="yes" (
    echo [SKIP] Skipping dependency installation (--skip-deps)
    echo.
    goto :verify
)

echo [1/5] Installing Python dependencies...
echo.

REM Build tools
pip install --upgrade pyinstaller

REM Core (required) - includes PySide6 for GUI
pip install PySide6 pydub mutagen requests tqdm

REM Identification backends
pip install pyacoustid musicbrainzngs shazamio

REM ACRCloud - install from GitHub (PyPI wheels don't exist for Windows)
echo.
echo    Installing ACRCloud SDK from GitHub...
pip install git+https://github.com/acrcloud/acrcloud_sdk_python 2>nul || (
    echo    [WARN] ACRCloud SDK install failed - trying fallback...
    pip install acrcloud-sdk-python 2>nul || echo    [WARN] ACRCloud SDK not available - ACRCloud mode disabled
)

REM Audio analysis and recording
pip install librosa numpy scipy numba soundfile soundcard sounddevice

REM Interactive terminal UI
pip install prompt_toolkit wcwidth

REM System utilities
pip install psutil
pip install pycaw comtypes

REM Async deps used by shazamio (explicit for PyInstaller bundling)
pip install aiohttp aiosignal frozenlist multidict yarl async_timeout attrs charset_normalizer

echo.
echo [OK] Python dependencies installed
echo.

REM ────────────────────────────────────────────────────────
REM STEP 2: Verify prerequisites
REM ────────────────────────────────────────────────────────
:verify

echo [2/5] Verifying prerequisites...
echo.

REM Check binaries in project folder
set MISSING=no

if not exist "ffmpeg.exe" (
    echo    [ERROR] ffmpeg.exe not found in current directory!
    echo           Download from: https://github.com/BtbN/FFmpeg-Builds/releases
    set MISSING=yes
) else (
    echo    [OK] ffmpeg.exe
)

if not exist "ffprobe.exe" (
    echo    [ERROR] ffprobe.exe not found in current directory!
    echo           Download from: https://github.com/BtbN/FFmpeg-Builds/releases
    set MISSING=yes
) else (
    echo    [OK] ffprobe.exe
)

if not exist "fpcalc.exe" (
    echo    [WARN] fpcalc.exe not found - AcoustID/MusicBrainz fingerprinting disabled
    echo           Download from: https://acoustid.org/chromaprint
) else (
    echo    [OK] fpcalc.exe
)

echo    Ensuring icon.ico is high-resolution...
if exist "mixsplitr_icon_512.png" (
    python "%COMPILERS_DIR%create_windows_icon.py" icon.ico --source mixsplitr_icon_512.png
) else (
    python "%COMPILERS_DIR%create_windows_icon.py" icon.ico
)

if errorlevel 1 (
    echo    [WARN] icon.ico could not be validated/generated
    echo           EXE icon may appear blurry or fallback to default
) else (
    echo    [OK] icon.ico (multi-resolution)
)

REM Check UI assets
if not exist "mixsplitr.png" (
    echo    [ERROR] mixsplitr.png not found - GUI wordmark missing!
    set MISSING=yes
) else (
    echo    [OK] mixsplitr.png
)

if not exist "mixsplitr_icon_512.png" (
    echo    [WARN] mixsplitr_icon_512.png not found - GUI app icon missing
) else (
    echo    [OK] mixsplitr_icon_512.png
)

if "%MISSING%"=="yes" (
    echo.
    echo    Required files missing! Please download and place them in this folder.
    pause
    exit /b 1
)

REM Verify critical Python packages
echo.
echo    Checking Python packages...
python -c "import PySide6" 2>nul || (echo    [ERROR] PySide6 not installed & set MISSING=yes)
python -c "import pydub" 2>nul || (echo    [ERROR] pydub not installed & set MISSING=yes)
python -c "import mutagen" 2>nul || (echo    [ERROR] mutagen not installed & set MISSING=yes)
python -c "import requests" 2>nul || (echo    [ERROR] requests not installed & set MISSING=yes)
python -c "import tqdm" 2>nul || (echo    [ERROR] tqdm not installed & set MISSING=yes)

if "%MISSING%"=="yes" (
    echo.
    echo    Required Python packages missing! Run without --skip-deps to install.
    pause
    exit /b 1
)

echo    [OK] All required packages present
echo.

REM Check optional packages (warn only)
python -c "from acrcloud.recognizer import ACRCloudRecognizer" 2>nul || echo    [WARN] acrcloud not installed (ACRCloud mode disabled)
python -c "import acoustid" 2>nul || echo    [WARN] acoustid not installed
python -c "import musicbrainzngs" 2>nul || echo    [WARN] musicbrainzngs not installed
python -c "import shazamio" 2>nul || echo    [WARN] shazamio not installed
python -c "import librosa" 2>nul || echo    [WARN] librosa not installed
python -c "import psutil" 2>nul || echo    [WARN] psutil not installed
python -c "import pycaw" 2>nul || echo    [WARN] pycaw not installed (app audio session discovery disabled)
python -c "import comtypes" 2>nul || echo    [WARN] comtypes not installed (app audio session discovery disabled)
python -c "import prompt_toolkit" 2>nul || echo    [WARN] prompt_toolkit not installed
python -c "import soundcard" 2>nul || echo    [WARN] soundcard not installed
python -c "import soundfile" 2>nul || echo    [WARN] soundfile not installed
python -c "import sounddevice" 2>nul || echo    [WARN] sounddevice not installed
python -c "import aiohttp" 2>nul || echo    [WARN] aiohttp not installed

REM ────────────────────────────────────────────────────────
REM STEP 3: Build optional Windows app-capture helper
REM ────────────────────────────────────────────────────────

echo.
echo [3/5] Building Windows app-capture helper...

set "APP_CAPTURE_HELPER_SRC=%COMPILERS_DIR%windows_process_loopback\ProcessLoopbackCaptureHelper.cpp"
set APP_CAPTURE_HELPER_EXE=mixsplitr_process_loopback.exe
set APP_CAPTURE_HELPER_BUILT=no
set APP_CAPTURE_HELPER_LOG=app_capture_build.log

if not exist "%APP_CAPTURE_HELPER_SRC%" (
    echo    [ERROR] %APP_CAPTURE_HELPER_SRC% not found
    echo            Windows builds now require the app-capture helper source
    popd
    pause
    exit /b 1
)

where cl >nul 2>nul
if errorlevel 1 (
    echo    [ERROR] Visual C++ compiler not found
    echo            Open a Developer Command Prompt / Developer PowerShell for Visual Studio
    echo            or run vcvars64.bat first, then rerun this build
    popd
    pause
    exit /b 1
)

if exist "%APP_CAPTURE_HELPER_EXE%" (
    del /f /q "%APP_CAPTURE_HELPER_EXE%" >nul 2>nul
)

if exist "%APP_CAPTURE_HELPER_LOG%" (
    del /f /q "%APP_CAPTURE_HELPER_LOG%" >nul 2>nul
)

cl /nologo /std:c++17 /EHsc /O2 /DUNICODE /D_UNICODE /DWIN32_LEAN_AND_MEAN /Fe:"%APP_CAPTURE_HELPER_EXE%" "%APP_CAPTURE_HELPER_SRC%" /link mmdevapi.lib ole32.lib avrt.lib user32.lib > "%APP_CAPTURE_HELPER_LOG%" 2>&1
if errorlevel 1 (
    echo    [ERROR] Failed to compile %APP_CAPTURE_HELPER_EXE%
    echo            Windows builds now require the app-capture helper
    echo.
    if exist "%APP_CAPTURE_HELPER_LOG%" (
        type "%APP_CAPTURE_HELPER_LOG%"
    )
    popd
    pause
    exit /b 1
)

echo    [OK] %APP_CAPTURE_HELPER_EXE%
set APP_CAPTURE_HELPER_BUILT=yes

if /I not "%APP_CAPTURE_HELPER_BUILT%"=="yes" (
    echo    [ERROR] %APP_CAPTURE_HELPER_EXE% was not produced
    popd
    pause
    exit /b 1
)

REM ────────────────────────────────────────────────────────
REM STEP 4: Verify all module files exist
REM ────────────────────────────────────────────────────────

echo.
echo [4/5] Checking module files...

set ALL_MODULES=yes
for %%f in (
    main_ui.py
    mixsplitr.py
    mixsplitr_core.py
    mixsplitr_identify.py
    mixsplitr_metadata.py
    mixsplitr_tagging.py
    mixsplitr_editor.py
    mixsplitr_audio.py
    mixsplitr_tracklist.py
    mixsplitr_record.py
    mixsplitr_manifest.py
    mixsplitr_memory.py
    mixsplitr_menu.py
    mixsplitr_menus.py
    mixsplitr_processing.py
    mixsplitr_pipeline.py
    mixsplitr_session.py
    mixsplitr_autotracklist.py
    mixsplitr_cdrip.py
    mixsplitr_process_capture.py
    splitter_ui.py
) do (
    if not exist "%%f" (
        echo    [WARN] %%f not found
        set ALL_MODULES=no
    )
)

if "%ALL_MODULES%"=="yes" (
    echo    [OK] All 21 module files found
) else (
    echo    [WARN] Some module files missing - build may have reduced functionality
)

echo.

REM ────────────────────────────────────────────────────────
REM STEP 5: Build with PyInstaller
REM ────────────────────────────────────────────────────────

echo [5/5] Building package with PyInstaller...
echo    Using spec: %SPEC_FILE%
echo.

REM Clean previous builds
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM Build using the spec file (contains all hidden imports and collect_all)
python -m PyInstaller --clean --noconfirm "%SPEC_FILE%"

echo.

if exist "%OUTPUT_PATH%" (
    echo ========================================================
    echo   Build Complete!
    echo ========================================================
    echo.
    echo   Output: %OUTPUT_PATH%
    echo.
    echo   To test: %OUTPUT_PATH%
    echo ========================================================
) else (
    echo ========================================================
    echo   BUILD FAILED!
    echo   Check the output above for errors.
    echo ========================================================
    goto :done
)

if /I "%BUILD_INSTALLER%"=="yes" (
    if /I "%BUILD_MODE%"=="onefile" (
        echo.
        echo    [WARN] --installer requires onedir output; skipping installer build
        goto :done
    )

    echo.
    echo [6/6] Building Inno Setup installer...

    set "ISCC_PATH="
    where ISCC >nul 2>nul && for /f "delims=" %%I in ('where ISCC') do set "ISCC_PATH=%%I"
    if not defined ISCC_PATH if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    if not defined ISCC_PATH if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe"

    if not defined ISCC_PATH (
        echo    [ERROR] Inno Setup compiler not found
        echo            Install Inno Setup 6 and rerun with --installer
        goto :done
    )

    set "APP_VERSION=8.0"
    for /f "tokens=2 delims==" %%V in ('findstr /B /C:"CURRENT_VERSION" mixsplitr_core.py') do (
        set "APP_VERSION=%%~V"
    )
    set "APP_VERSION=!APP_VERSION: =!"
    set "APP_VERSION=!APP_VERSION:"=!"

    "!ISCC_PATH!" /DMyAppVersion=!APP_VERSION! "%COMPILERS_DIR%MixSplitR_ONEDIR_Installer.iss"
    if errorlevel 1 (
        echo    [ERROR] Inno Setup build failed
        goto :done
    )
    echo    [OK] Installer built in dist\installers\
)

:done
echo.
popd
pause
