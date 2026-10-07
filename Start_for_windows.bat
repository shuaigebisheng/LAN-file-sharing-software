@echo off
setlocal
cd /d "%~dp0"
title LAN Transfer

echo ============================================
echo   LAN Transfer launcher
echo ============================================
echo   dir: %CD%
echo.

REM ---------- python ----------
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if defined PY (echo [ok] python: %PY%) else (echo [--] python: not found)

REM ---------- exe: scan dist\*.exe then .\*.exe ----------
set "EXE="
for %%F in ("dist\*.exe") do (
    if not defined EXE if exist "%%~fF" set "EXE=%%~fF"
)
if not defined EXE (
    for %%F in ("*.exe") do (
        if not defined EXE if exist "%%~fF" set "EXE=%%~fF"
    )
)
if defined EXE (echo [ok] exe: %EXE%) else (echo [--] exe: not found)
echo.

REM ---------- mode ----------
if /i "%~1"=="--src" goto run_src
if /i "%~1"=="--exe" goto run_exe

REM auto: prefer source if python exists (always latest)
if defined PY  goto run_src
if defined EXE goto run_exe

echo [X] neither python nor exe found.
pause
exit /b 1

:run_src
if not exist "transfer.py" (
    echo [X] missing transfer.py
    pause
    exit /b 1
)
echo [start] source mode
echo.
%PY% transfer.py
echo.
echo [done] program exited.
pause
exit /b 0

:run_exe
if not defined EXE (
    echo [X] no exe found
    pause
    exit /b 1
)
echo [start] %EXE%
start "" "%EXE%"
exit /b 0