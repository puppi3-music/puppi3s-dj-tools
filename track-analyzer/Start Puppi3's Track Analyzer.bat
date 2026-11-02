@echo off
rem Double-click this in File Explorer to start Puppi3's Track Analyzer: the first time it installs what it needs (uv,
rem Python and the analyzer), then it opens the web page, where the Setup tab takes it from there.
rem Keep this window open while you use the page; close it (or press Ctrl-C) to stop.
rem The Windows twin of "Start Puppi3's Track Analyzer.command" (macOS, Linux).

setlocal
cd /d "%~dp0" || goto :eof
set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
rem Python's text files and output in UTF-8, not the ANSI code page (accented track names)
set "PYTHONUTF8=1"
set "URL=http://127.0.0.1:8766/"

curl.exe -fs -o NUL "%URL%api/libraries" >NUL 2>&1
if not errorlevel 1 (
  echo Puppi3's Track Analyzer is already running: opening the page.
  start "" "%URL%"
  exit /b 0
)

rem Windows on Arm: the analyzer's audio library and TensorFlow have x64 builds only, which Windows runs emulated
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "UV_PYTHON=cpython-3.11-windows-x86_64-none"

where uv >NUL 2>&1
if errorlevel 1 (
  echo Installing uv, the Python manager the analyzer runs with ^(from astral.sh^)...
  powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)
where uv >NUL 2>&1
if errorlevel 1 (
  set "MSG=Couldn't install uv. Check the internet connection and try again, or close this window and double-click again."
  goto :fail
)

echo Getting Python and the analyzer ready (the first time takes a few minutes)...
uv sync --inexact
if errorlevel 1 (
  set "MSG=Couldn't install the analyzer's Python packages."
  goto :fail
)

echo.
echo Starting Puppi3's Track Analyzer. The page opens in your browser.
echo Keep this window open while you use it; close it (or press Ctrl-C) to stop.
echo.
uv run dj-track-analyzer web
exit /b %errorlevel%

:fail
echo.
echo %MSG%
echo README.md, section 1, has the same steps to do by hand.
pause
exit /b 1
