#!/usr/bin/env python3
"""
Диагностика реального сканера Regula 7017 (Desktop SDK).

Запускается на Windows-ПК, к которому подключён сканер Regula 7017 и установлен
Regula Document Reader **Desktop SDK** (.dll + Python-обёртка) с активной
лицензией. Скрипт:

  1. проверяет наличие SDK, .dll и лицензии;
  2. инициализирует SDK и перечисляет устройства;
  3. делает один захват + распознавание паспорта;
  4. печатает найденные поля (MRZ, ФИО, гражданство, даты, VIZ);
  5. сохраняет снимок паспорта и портрет в папку вывода.

Весь вывод (текст в консоли + файлы из папки вывода) нужно прислать разработчику
— по нему сверяются точные имена классов/полей под установленную версию SDK.

Запуск (Windows, cmd):

    set ARM_REGULA_DLL=C:\\Program Files\\Regula\\DocumentReaderSDK\\bin
    set ARM_REGULA_LICENSE=C:\\Program Files\\Regula\\license\\regula.license
    python tools\\regula_selftest.py

Скрипт самодостаточен: не требует GUI и не меняет конфигурацию программы.
"""
from __future__ import annotations

import os
import sys

# Чтобы скрипт работал из любой папки — добавляем корень проекта в путь.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from armcore import scanners  # noqa: E402


def _hr(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main() -> int:
    out_dir = os.path.join(_HERE, "regula_selftest_out")
    dll = os.environ.get("ARM_REGULA_DLL", "")
    lic = os.environ.get("ARM_REGULA_LICENSE", "")

    _hr("1. Параметры окружения")
    print(f"ARM_REGULA_DLL      = {dll or '(не задан)'}")
    print(f"ARM_REGULA_LICENSE  = {lic or '(не задан)'}")
    print(f"Папка вывода        = {out_dir}")
    if lic:
        print(f"Файл лицензии есть?  = {os.path.exists(lic)}")

    scanner = scanners.RegulaScanner(dll_path=dll or None, license_path=lic or None)

    _hr("2. Инициализация SDK")
    try:
        scanner._load_sdk()
        print("OK: SDK инициализирован.")
    except scanners.ScannerError as e:
        print("ОШИБКА инициализации SDK:")
        print(" ", e)
        print("\nДальше двигаться нельзя — пришлите этот вывод разработчику.")
        return 2

    _hr("3. Захват и распознавание паспорта")
    print("Положите паспорт на сканер и подождите...")
    try:
        cap = scanner.capture_passport(out_dir)
    except scanners.ScannerError as e:
        print("ОШИБКА захвата/распознавания:")
        print(" ", e)
        print("\nПришлите этот вывод разработчику (возможно, отличается версия API SDK).")
        return 3

    _hr("4. Результат распознавания")
    print("MRZ (сырой текст):")
    print(cap.mrz_text or "  (пусто)")
    print("\nПоля визуальной зоны (VIZ):")
    if cap.viz_fields:
        for k, v in cap.viz_fields.items():
            print(f"  {k}: {v}")
    else:
        print("  (пусто)")

    print("\nСохранённые изображения:")
    if cap.image_paths:
        for p in cap.image_paths:
            print(f"  {p}")
    else:
        print("  (нет — SDK не вернул графические поля)")

    # Разбор MRZ общим парсером — как в программе.
    if cap.mrz_text:
        from armcore import mrz as mrz_parser
        res = mrz_parser.parse(cap.mrz_text)
        _hr("5. Разбор MRZ парсером программы")
        if res:
            for k, v in res.to_fields().items():
                print(f"  {k}: {v}")
            print(f"\n  Контрольные цифры сошлись: {res.valid}")
        else:
            print("  Парсер не смог разобрать MRZ (проверьте формат).")

    _hr("ГОТОВО")
    print("Скопируйте весь вывод выше и содержимое папки вывода —")
    print(f"  {out_dir}")
    print("и пришлите разработчику для финальной настройки.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
