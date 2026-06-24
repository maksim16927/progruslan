@echo off
chcp 65001 >nul
title АРМ — Тест сканера Regula
cd /d "%~dp0"

rem === Укажите пути к Regula Document Reader Desktop SDK и лицензии ===
rem (поправьте под свою установку, если отличается)
set ARM_REGULA_DLL=C:\Program Files\Regula\DocumentReaderSDK\bin
set ARM_REGULA_LICENSE=C:\Program Files\Regula\license\regula.license

echo Запуск диагностики сканера Regula 7017...
echo Положите паспорт на сканер.
echo.
python tools\regula_selftest.py

echo.
echo Скопируйте весь текст выше и содержимое папки tools\regula_selftest_out
echo и пришлите разработчику.
pause
