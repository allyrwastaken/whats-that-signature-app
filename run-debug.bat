@echo off
rem Run from source with --debug: prints every OCR read to this console.
cd /d "%~dp0"
echo Running in debug mode. Watch this window for OCR reads.
echo.
".venv\Scripts\python.exe" overlay.py --debug
pause
