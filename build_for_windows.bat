@echo off
setlocal
cd /d "%~dp0"
title LAN Transfer - Build

set "NO_PAUSE="
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"

echo ============================================
echo   LAN Transfer - Build
echo ============================================
echo.

REM ---- 1. python ----
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [X] Python not found. Install Python 3.8+
    if not defined NO_PAUSE pause
    exit /b 1
)
echo [1/4] Python:
%PY% --version
echo.

REM ---- 2. files ----
echo [2/4] Checking project files...
for %%F in (transfer.py qr.py web\index.html LanTransfer.spec) do (
    if not exist "%%F" (
        echo [X] missing: %%F
        if not defined NO_PAUSE pause
        exit /b 1
    )
)
echo       OK
echo.

REM ---- 3. clean ----
echo [3/4] Cleaning old build...
if exist "build" rd /s /q "build"
if exist "dist"  rd /s /q "dist"
echo       OK
echo.

REM ---- 4. build ----
echo [4/4] Building (first run may take 1-3 minutes)...
echo.
%PY% -m PyInstaller --noconfirm --clean LanTransfer.spec

if errorlevel 1 (
    echo.
    echo [X] build failed
    if not defined NO_PAUSE pause
    exit /b 1
)

echo.
echo ============================================
echo   Build OK
echo   Output: dist\LanTransfer.exe
echo ============================================
echo.
if not defined NO_PAUSE pause
exit /b 0