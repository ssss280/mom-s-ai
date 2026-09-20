@echo off
rem ============================================================
rem  ChatSight - one click setup for a fresh Windows machine
rem  - installs Python if missing (per user, no admin needed)
rem  - installs the dependencies from requirements.txt
rem  - starts the app (unless --no-start is given)
rem
rem  NOTE 1: keep this file ASCII-only. Chinese text + cmd code page
rem          switching is what broke the old launcher before.
rem  NOTE 2: never put an unescaped ")" inside "if (...)" blocks,
rem          not even inside quotes - cmd ends the block there.
rem  NOTE 3: keep the interpreter path and its arguments in separate
rem          variables. "%PY%" with PY="py -3" becomes the literal
rem          command name "py -3" and cmd cannot find it.
rem ============================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title ChatSight Setup

set "APPNAME=ChatSight"
set "PYVER=3.12.10"
set "PYSHORT=312"
set "OFFICIAL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe"
set "MIRROR=https://mirrors.huaweicloud.com/python/%PYVER%/python-%PYVER%-amd64.exe"
set "INSTALLER=%TEMP%\python-%PYVER%-amd64.exe"
set "PIPMIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
set "NOSTART="
if "%~1"=="--no-start" set "NOSTART=1"

echo ============================================================
echo   %APPNAME% setup
echo ============================================================
echo.

if not exist "requirements.txt" (
  echo [ERROR] requirements.txt not found.
  echo         Please run this script from the ChatSight folder,
  echo         the folder that contains server.py.
  goto fail
)

call :find_python
if defined PYEXE goto deps

echo [1/4] Python was not found on this computer.
echo       Installing Python %PYVER% for the current user only,
echo       no administrator rights needed.
echo.
echo [2/4] Downloading the installer, about 26 MB ...
call :download "%OFFICIAL%" "%INSTALLER%"
if not exist "%INSTALLER%" (
  echo       Official site did not work, trying a mirror ...
  call :download "%MIRROR%" "%INSTALLER%"
)
if not exist "%INSTALLER%" (
  echo.
  echo [ERROR] Could not download the Python installer.
  echo         Download it manually from one of these links:
  echo           %OFFICIAL%
  echo           %MIRROR%
  echo         Run it, tick "Add python.exe to PATH", then run this script again.
  goto fail
)

echo [3/4] Installing Python, please wait ...
"%INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1 Include_test=0 AssociateFiles=0 Shortcuts=0
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" if not "%RC%"=="3010" (
  echo [ERROR] The Python installer returned code %RC%.
  del "%INSTALLER%" >nul 2>nul
  goto fail
)
del "%INSTALLER%" >nul 2>nul

call :find_python
if not defined PYEXE (
  echo [ERROR] Python was installed but could not be located.
  echo         Close this window and run this script again.
  goto fail
)

:deps
echo [4/4] Using Python: %PYEXE% %PYARGS%
"%PYEXE%" %PYARGS% -c "import sys; print('      version ' + sys.version.split()[0])"

echo       Installing dependencies: flask, mss, Pillow, openai, pytesseract ...
"%PYEXE%" %PYARGS% -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo       Default package index failed, retrying with a China mirror ...
  "%PYEXE%" %PYARGS% -m pip install --disable-pip-version-check -q -i "%PIPMIRROR%" -r requirements.txt
)
if errorlevel 1 (
  echo.
  echo [ERROR] Installing the dependencies failed.
  echo         Check your network or proxy, then run this script again.
  goto fail
)

"%PYEXE%" %PYARGS% -c "import flask, mss, PIL, openai, pytesseract" >nul 2>nul
if errorlevel 1 (
  echo.
  echo [ERROR] Some dependency is still missing after the install.
  goto fail
)

echo.
echo Setup finished.
if defined NOSTART (
  echo --no-start was given, so the app is not launched now.
  echo Just double click the start script next to this file later.
  pause
  exit /b 0
)

echo Starting %APPNAME% ...
echo A browser page will open at http://127.0.0.1:5050
echo Keep this window open while you use it. Ctrl+C stops the server.
echo.
"%PYEXE%" %PYARGS% server.py

echo.
echo %APPNAME% has stopped.
echo Optional: install Tesseract-OCR if you want the offline OCR engine.
pause
exit /b 0


rem ------------------------------------------------------------
rem  locate a usable Python, 3.9 or newer
rem  result: PYEXE = interpreter, PYARGS = extra arguments
rem ------------------------------------------------------------
:find_python
set "PYEXE="
set "PYARGS="

rem 1. the official py launcher, avoids the Microsoft Store alias
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PYEXE=py"
  set "PYARGS=-3"
  exit /b 0
)

rem 2. a python on PATH, skipping the Microsoft Store alias
for /f "delims=" %%W in ('where python 2^>nul') do call :consider "%%W"
if defined PYEXE exit /b 0

rem 3. standard install locations, also covers just-installed-but-PATH-not-refreshed
if exist "%LOCALAPPDATA%\Programs\Python\Python%PYSHORT%\python.exe" call :consider "%LOCALAPPDATA%\Programs\Python\Python%PYSHORT%\python.exe"
if not defined PYEXE if exist "%ProgramFiles%\Python%PYSHORT%\python.exe" call :consider "%ProgramFiles%\Python%PYSHORT%\python.exe"
set "PF86=%ProgramFiles(x86)%"
if not defined PYEXE if defined PF86 if exist "%PF86%\Python%PYSHORT%\python.exe" call :consider "%PF86%\Python%PYSHORT%\python.exe"
exit /b 0

:consider
rem %1 = candidate python.exe. Accept only if it runs and is 3.9 or newer.
if defined PYEXE exit /b 0
if not exist "%~1" exit /b 0
echo %~1| find /i "WindowsApps" >nul
if not errorlevel 1 exit /b 0
"%~1" -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PYEXE=%~1"
exit /b 0


rem ------------------------------------------------------------
rem  download %1 into %2, curl first and PowerShell as fallback
rem ------------------------------------------------------------
:download
if exist "%~2" del "%~2" >nul 2>nul
where curl >nul 2>nul
if not errorlevel 1 (
  curl -L --fail --connect-timeout 20 --retry 2 -o "%~2" "%~1"
  call :check_size "%~2"
  if exist "%~2" exit /b 0
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri '%~1' -OutFile '%~2' -UseBasicParsing } catch { exit 1 }"
call :check_size "%~2"
exit /b 0

:check_size
rem a real installer is around 25 MB, so drop anything much smaller
if not exist "%~1" exit /b 0
for %%A in ("%~1") do if %%~zA LSS 10000000 (
  echo       The downloaded file looks incomplete, discarding it.
  del "%~1" >nul 2>nul
)
exit /b 0


:fail
echo.
echo Setup failed.
pause
exit /b 1
