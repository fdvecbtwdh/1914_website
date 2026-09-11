@echo off
rem 1914.fun one-click launcher: starts server in background, health-checks, opens browser
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" -OpenBrowser
pause
