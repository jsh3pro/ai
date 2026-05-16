@echo off
ffmpeg -fflags +genpts -i "%~1" -c copy -movflags +faststart "%~dpn1_fixed.mp4"
pause
