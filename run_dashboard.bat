@echo off
REM Double-click this file to build the dashboard now.
cd /d "%~dp0"
where py >nul 2>nul && (py market_pulse.py %*) || (python market_pulse.py %*)
if errorlevel 1 pause
