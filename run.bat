@echo off
rem Run the overlay from source using the local .venv.
cd /d "%~dp0"
".venv\Scripts\python.exe" overlay.py
pause
