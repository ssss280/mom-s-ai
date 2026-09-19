@echo off
rem ---------------------------------------------------------------
rem ChatSight launcher
rem IMPORTANT: keep this file ASCII-only. cmd.exe mis-parses a .bat
rem that contains UTF-8 Chinese text together with a mid-file chcp,
rem which silently breaks the "start" line below.
rem ---------------------------------------------------------------
cd /d "%~dp0"

set "EXE_PATH=%~dp0build\exe.win-amd64-3.14\ChatSight.exe"

if not exist "%EXE_PATH%" (
    echo [ERROR] ChatSight.exe not found:
    echo         %EXE_PATH%
    echo Please run build.bat first.
    pause
    exit /b 1
)

rem Let the exe keep data\ / error\ / config.json in this source folder
rem instead of build\exe.win-amd64-3.14\
set "CHATSIGHT_HOME=%~dp0"

start "" "%EXE_PATH%"
exit