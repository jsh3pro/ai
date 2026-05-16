@echo off
setlocal

REM 시간에서 콜론(:)을 하이픈(-)으로 변환 (파일명에 ':' 못 씀)
set "start=%~2"
set "end=%~3"
set "start_safe=%start::=-%"
set "end_safe=%end::=-%"

ffmpeg -ss %~2 -to %~3 -i "%~1" -c copy "%~dpn1_%start_safe%_to_%end_safe%.mp4"

endlocal
pause
