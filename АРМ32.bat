@echo off
chcp 65001 >nul
title АРМ — Клиент (32-bit Python, для сканера документов)
cd /d "%~dp0"

rem Запуск под 32-битным Python — чтобы был виден TWAIN-драйвер сканера Kodak.
py -3-32 bary_de.py
if errorlevel 1 (
  echo.
  echo Ошибка запуска под 32-битным Python.
  echo Установите 32-битный Python и зависимости через Установка32.bat
  pause
)
