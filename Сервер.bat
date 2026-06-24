@echo off
chcp 65001 >nul
title АРМ — Сервер
cd /d "%~dp0"

rem Запуск общего сервера блокировок и БД клиентов.
echo Запуск сервера АРМ (порт 8770)...
python server\server.py --host 0.0.0.0 --port 8770 --db arm_server.db
if errorlevel 1 (
  echo.
  echo Ошибка запуска. Проверьте, установлен ли Python и зависимости:
  echo   pip install -r requirements.txt
  pause
)
