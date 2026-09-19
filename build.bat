@echo off
rem ------------------------------------------------------------------
rem ChatSight build script
rem IMPORTANT: keep this file ASCII-only. cmd.exe mis-parses a .bat that
rem mixes UTF-8 Chinese text with a mid-file chcp.
rem
rem Uses build_safe.py instead of setup.py: cx_Freeze's bytecode scanner
rem can crash on Python 3.14 (TypeError / access violation), build_safe.py
rem guards against it.
rem ------------------------------------------------------------------
cd /d "%~dp0"

set "PYTHON_CMD=py"
where py >nul 2>&1 || set "PYTHON_CMD=python"
where %PYTHON_CMD% >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python first.
    pause
    exit /b 1
)

echo [1/3] Checking dependencies...
%PYTHON_CMD% -m pip install cx_Freeze --quiet 2>nul

echo [2/3] Building, please wait...
%PYTHON_CMD% build_safe.py build

if errorlevel 1 (
    echo.
    echo [ERROR] build failed
    pause
    exit /b 1
)

echo.
echo [3/3] Build finished.
echo   Exe : build\exe.win-amd64-3.14\ChatSight.exe
echo   Run : double click ??.bat
echo.
pause