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

set "SECRET_OPTION="
if exist ".env" (
  ".buildenv\Scripts\python.exe" embed_secrets.py .env jarvis_secrets.dat
  set "SECRET_OPTION=--add-data jarvis_secrets.dat;."
) else if exist "jarvis_secrets.dat" (
  set "SECRET_OPTION=--add-data jarvis_secrets.dat;."
) else (
  echo ВНИМАНИЕ: .env не найден. Сборка будет без AI-ключей.
)

".buildenv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm --clean --onedir --windowed ^
  --name Jarvis ^
  --icon jarvis.ico ^
  --add-data "app.py;." ^
  %SECRET_OPTION% ^
  --add-data "jarvis.ico;." ^
  jarvis_entry.py

if exist "voise" xcopy /e /i /y "voise" "dist\Jarvis\voise" >nul
if exist "voice" xcopy /e /i /y "voice" "dist\Jarvis\voice" >nul

if exist "dist\Jarvis-windows.zip" del /q "dist\Jarvis-windows.zip"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\Jarvis\*' -DestinationPath 'dist\Jarvis-windows.zip' -Force"

echo.
echo Готово: dist\Jarvis\Jarvis.exe
echo Архив для GitHub Release: dist\Jarvis-windows.zip
if exist "jarvis_secrets.dat" del /q "jarvis_secrets.dat"
echo Папку voise положите в dist\Jarvis рядом с EXE, если она у вас есть.
echo GROQ_API_KEY и другие ключи не попадают в GitHub автоматически.
echo Если рядом есть .env, ключи зашифрованно встраиваются в личную сборку.
pause