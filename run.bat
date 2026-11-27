@echo off
title AutoClip AI - Viral Shorts Generator
echo ========================================================
echo        AutoClip AI - Viral 9:16 Shorts Generator
echo ========================================================
echo.

cd /d "%~dp0"

:: Ensure FFmpeg is accessible
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%B"
for /f "tokens=2*" %%A in ('reg query "HKLM\System\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%B"
set "PATH=%~dp0.venv\Scripts;%USER_PATH%;%SYS_PATH%;%PATH%"

if not exist ".venv\Scripts\python.exe" (
    echo [!] Virtual environment not found. Setting up...
    uv venv --python 3.11 .venv
    .\.venv\Scripts\python -m pip install fastapi "uvicorn[standard]" python-multipart yt-dlp faster-whisper opencv-python-headless httpx requests aiofiles pydantic jinja2
)

echo [*] Starting AutoClip AI Server...
echo [*] Waiting for server to initialize...

:: Start browser after brief pause so server binds first
start /b "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000"

:: Run Uvicorn server directly
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload

echo.
echo Server stopped.
pause
