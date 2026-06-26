@echo off
chcp 65001 >nul
title АРМ — Установка (32-bit Python)
cd /d "%~dp0"

echo Проверка 32-битного Python...
py -3-32 -c "import struct;print('Python', struct.calcsize('P')*8, 'бит')" || (
  echo.
  echo НЕ найден 32-битный Python.
  echo Скачайте его с https://www.python.org/downloads/windows/
  echo   пункт "Windows installer (32-bit)", при установке отметьте "Add to PATH".
  pause
  exit /b 1
)

echo.
echo Установка библиотек в 32-битный Python...
py -3-32 -m pip install --upgrade pip
py -3-32 -m pip install PyQt6 Pillow python-docx openpyxl pywin32 pytwain passporteye pytesseract opencv-python-headless
echo.
echo Готово. Запускайте программу через АРМ32.bat
pause
