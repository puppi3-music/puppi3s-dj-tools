@echo off
rem Double-click to start Puppi3's Track Analyzer (installs what it needs the first time). Twin of the .command.
rem Keep it under 1800 bytes (tests/test_update.py says why).
setlocal
cd /d "%~dp0" || goto :eof
set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
set "PYTHONUTF8=1"
set "TRACK_ANALYZER_LAUNCHER=1"
set "URL=http://127.0.0.1:8766/"
curl.exe -fs -o NUL "%URL%api/libraries" >NUL 2>&1
if not errorlevel 1 (
  echo Puppi3's Track Analyzer is already running: opening the page.
  start "" "%URL%"
  exit /b 0
)
rem Arm: x64 Python (the audio library and TensorFlow have x64 builds only)
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "UV_PYTHON=cpython-3.11-windows-x86_64-none"
where uv >NUL 2>&1 || powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
set "MSG=Couldn't install uv. Check the internet connection and try again."
where uv >NUL 2>&1 || goto :fail
set "OPEN="
:run
echo Getting Python and the analyzer ready (the first time takes a few minutes)...
set "MSG=Couldn't install the analyzer's Python packages."
uv sync --inexact || goto :fail
echo Starting Puppi3's Track Analyzer. The page opens in your browser. Close this window to stop it.
rem One block, read whole: an update may replace this file. 75 = restart after an update.
(
  uv run dj-track-analyzer web %OPEN%
  if errorlevel 75 if not errorlevel 76 (
    set "OPEN=--no-open"
    echo Restarting with the new version...
    goto :run
  )
  exit /b
)
:fail
echo.
echo %MSG%
echo README.md, section 1, has the same steps to do by hand.
pause
exit /b 1
