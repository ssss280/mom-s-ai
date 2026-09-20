@echo off
rem ChatSight web launcher - starts the local server and opens the browser.
cd /d "%~dp0"
py -3 server.py
pause
