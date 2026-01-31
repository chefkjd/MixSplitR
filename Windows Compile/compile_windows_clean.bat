@echo off
echo ========================================
echo   MixSplitR Windows CLEAN Build
echo ========================================
echo.

echo Cleaning old build files...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "MixSplitR.spec" del /q "MixSplitR.spec"
if exist "__pycache__" rmdir /s /q "__pycache__"
echo [OK] Clean complete

echo.
echo Installing required packages...
pip install pydub mutagen acrcloud requests tqdm psutil pyacoustid musicbrainzngs pyinstaller

echo.
echo Checking for required binaries...

REM Check ffmpeg
if not exist "ffmpeg.exe" (
    echo [ERROR] ffmpeg.exe not found in current directory!
    pause
    exit /b 1
)
echo [OK] Found ffmpeg.exe

REM Check ffprobe
if not exist "ffprobe.exe" (
    echo [ERROR] ffprobe.exe not found in current directory!
    pause
    exit /b 1
)
echo [OK] Found ffprobe.exe

REM Check icon (optional)
if not exist "icon.ico" (
    echo [WARNING] icon.ico not found - executable will use default icon
    set INCLUDE_ICON=no
) else (
    echo [OK] Found icon.ico
    set INCLUDE_ICON=yes
)

REM Check fpcalc (optional)
if not exist "fpcalc.exe" (
    echo [WARNING] fpcalc.exe not found - MusicBrainz fallback will be disabled
    set INCLUDE_FPCALC=no
) else (
    echo [OK] Found fpcalc.exe
    set INCLUDE_FPCALC=yes
)

echo.
echo Building executable with ACRCloud support...

REM Build with different combinations of icon and fpcalc
if "%INCLUDE_FPCALC%"=="yes" (
    if "%INCLUDE_ICON%"=="yes" (
        echo Building with MusicBrainz support and custom icon...
        python -m PyInstaller --onefile ^
            --hidden-import=acrcloud ^
            --hidden-import=acrcloud.recognizer ^
            --collect-all=acrcloud ^
            --collect-all=pyacrcloud ^
            --add-binary "ffmpeg.exe;." ^
            --add-binary "ffprobe.exe;." ^
            --add-binary "fpcalc.exe;." ^
            --icon "icon.ico" ^
            --name MixSplitR MixSplitR.py
    ) else (
        echo Building with MusicBrainz support...
        python -m PyInstaller --onefile ^
            --hidden-import=acrcloud ^
            --hidden-import=acrcloud.recognizer ^
            --collect-all=acrcloud ^
            --collect-all=pyacrcloud ^
            --add-binary "ffmpeg.exe;." ^
            --add-binary "ffprobe.exe;." ^
            --add-binary "fpcalc.exe;." ^
            --name MixSplitR MixSplitR.py
    )
) else (
    if "%INCLUDE_ICON%"=="yes" (
        echo Building without MusicBrainz support, with custom icon...
        python -m PyInstaller --onefile ^
            --hidden-import=acrcloud ^
            --hidden-import=acrcloud.recognizer ^
            --collect-all=acrcloud ^
            --collect-all=pyacrcloud ^
            --add-binary "ffmpeg.exe;." ^
            --add-binary "ffprobe.exe;." ^
            --icon "icon.ico" ^
            --name MixSplitR MixSplitR.py
    ) else (
        echo Building without MusicBrainz support...
        python -m PyInstaller --onefile ^
            --hidden-import=acrcloud ^
            --hidden-import=acrcloud.recognizer ^
            --collect-all=acrcloud ^
            --collect-all=pyacrcloud ^
            --add-binary "ffmpeg.exe;." ^
            --add-binary "ffprobe.exe;." ^
            --name MixSplitR MixSplitR.py
    )
)

echo.
echo ========================================
echo Compilation complete! Check the 'dist' folder for your executable.
echo ========================================
pause
