@echo off
REM Windows Compilation Script for MixSplitacr
REM Make sure you have ffmpeg.exe and ffprobe.exe in the same directory

python -m PyInstaller --onefile ^
--icon="icon.ico" ^
--add-binary "ffmpeg.exe;." ^
--add-binary "ffprobe.exe;." ^
--collect-submodules acrcloud ^
--hidden-import mutagen.flac ^
--hidden-import requests ^
MixSplitacr.py

echo.
echo Compilation complete! Check the 'dist' folder for your executable.
pause
