@echo off
setlocal
cd /d "%~dp0"
title Jarvis build
set "JARVIS_GITHUB_REPO=gogosha11/Jarvis"

where py >nul 2>&1
if errorlevel 1 (
  echo Не найден Python. Установите Python 3.10+ с python.org и включите Add Python to PATH.
  pause
  exit /b 1
)

if not exist ".buildenv\Scripts\python.exe" py -m venv .buildenv
".buildenv\Scripts\python.exe" -m pip install --upgrade pip
".buildenv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller

if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"

".buildenv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm --clean --onedir --windowed ^
  --name Jarvis ^
  --icon jarvis.ico ^
  --add-data "app.py;." ^
  --add-data "jarvis.ico;." ^
  jarvis_entry.py

if exist "voise" xcopy /e /i /y "voise" "dist\Jarvis\voise" >nul
if exist "voice" xcopy /e /i /y "voice" "dist\Jarvis\voice" >nul

if exist "dist\Jarvis-windows.zip" del /q "dist\Jarvis-windows.zip"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\Jarvis\*' -DestinationPath 'dist\Jarvis-windows.zip' -Force"

echo.
echo Готово: dist\Jarvis\Jarvis.exe
echo Архив для GitHub Release: dist\Jarvis-windows.zip
echo Папку voise положите в dist\Jarvis рядом с EXE, если она у вас есть.
echo API-ключи не копируются в dist: Jarvis использует Railway backend.
pause