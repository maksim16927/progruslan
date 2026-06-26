@echo off
chcp 65001 >nul
title АРМ — Установка 32-bit помощника сканера
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
echo Установка pytwain в 32-битный Python (только он нужен помощнику сканера)...
py -3-32 -m pip install --upgrade pip
py -3-32 -m pip install pytwain
echo.
echo Готово. Основную программу запускайте как обычно (АРМ.bat, 64-бит).
echo Сканирование документа само вызовет 32-битный помощник.
pause
