@echo off
chcp 65001 >nul
title АРМ — Установка зависимостей
cd /d "%~dp0"

echo Установка библиотек Python для АРМ...
python -m pip install -r requirements.txt
echo.
echo Для распознавания паспорта из файла дополнительно нужен Tesseract OCR:
echo   https://github.com/UB-Mannheim/tesseract/wiki
echo.
pause
