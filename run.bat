@echo off
setlocal enabledelayedexpansion
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title ClipForge Local AI Studio

cd /d "%~dp0"

echo ===================================================
echo               ClipForge Launcher
echo ===================================================

rem 1. Check local FFmpeg or PATH
if exist "%~dp0tools\ffmpeg\bin\ffmpeg.exe" (
    set "PATH=%~dp0tools\ffmpeg\bin;%PATH%"
    echo [*] Using project-local FFmpeg from tools\ffmpeg\bin
    goto :ffmpeg_done
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [ERROR] FFmpeg not found!
    echo Please ensure FFmpeg is deployed to tools\ffmpeg\bin\ or installed on PATH.
    echo You can run 'python scripts/deploy_ffmpeg.py' to download the portable build.
    goto :fail
)
echo [*] Using system FFmpeg from PATH

:ffmpeg_done

rem 2. Check virtual environment
if exist ".venv\Scripts\python.exe" goto :venv_ready

echo [!] Virtual environment not found.
echo [*] Searching for Python 3.11...
set "PY311="
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PY311=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) else (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3.11 --version >nul 2>&1
        if not errorlevel 1 set "PY311=py -3.11"
    )
)

if "!PY311!"=="" (
    echo [ERROR] Python 3.11 was not found on your system!
    echo Please install Python 3.11.9 from python.org or check its installation path.
    goto :fail
)

echo [*] Creating virtual environment with !PY311!...
!PY311! -m venv .venv
if errorlevel 1 (
    echo [ERROR] Failed to create .venv!
    goto :fail
)
echo [*] Installing requirements into .venv...
.venv\Scripts\python.exe -m pip install --no-input --upgrade pip
.venv\Scripts\pip.exe install --no-input -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed!
    goto :fail
)

:venv_ready

rem 3. Verify .venv Python executable
.venv\Scripts\python.exe --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] .venv\Scripts\python.exe is corrupted or not executable.
    goto :fail
)

rem 4. Ensure workspace directories exist
if not exist "output" mkdir output
if not exist "cache" mkdir cache
if not exist "downloads" mkdir downloads

rem 5. Add Ollama to PATH if in default local directory
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
)

rem 6. Launch application entry point
echo [*] Starting ClipForge server on http://127.0.0.1:7860...
echo [*] Logs will stream below. Close this window to stop the server.
echo.
.venv\Scripts\python.exe -m server.main

if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo ===================================================
echo [ERROR] ClipForge failed to start or exited with an error.
echo ===================================================
echo Common solutions:
echo  1. Run PowerShell: .\check-environment.ps1 to diagnose missing components.
echo  2. Ensure Python 3.11.9 is installed.
echo  3. Ensure tools\ffmpeg\bin\ffmpeg.exe is present.
echo  4. If port 7860 is occupied, close the previous terminal window.
echo ===================================================
echo.
pause
exit /b 1
