@echo off
chcp 65001 >nul
title АРМ — Клиент
cd /d "%~dp0"

rem Запуск клиента АРМ (графический интерфейс оператора).
python bary_de.py
if errorlevel 1 (
  echo.
  echo Ошибка запуска. Проверьте, установлен ли Python и зависимости:
  echo   pip install -r requirements.txt
  pause
)
