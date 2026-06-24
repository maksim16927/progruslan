#!/usr/bin/env python3
"""
Диагностика сканера Regula 7017 (COM-объект READERDEMO.RegulaReader).

Поставка Regula Passport Reader SDK регистрирует COM-компонент
``READERDEMO.RegulaReader``. Скрипт подключается к нему через pywin32 и:

  1. создаёт COM-объект (проверка, что SDK установлен/зарегистрирован);
  2. РЕЖИМ А (по умолчанию): ждёт, пока оператор положит паспорт на сканер;
     РЕЖИМ Б (если передать путь к скану): распознаёт файл через DoProcessImage;
  3. печатает MRZ и поля визуальной зоны (ФИО, даты, орган и т.д.);
  4. сохраняет портрет в папку вывода.

Запуск (Windows):

    pip install pywin32
    rem с устройства (положите паспорт):
    py tools\\regula_selftest.py
    rem из готового скана (без устройства):
    py tools\\regula_selftest.py C:\\путь\\к\\скану.jpg

Весь вывод + папку tools\\regula_selftest_out пришлите разработчику.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from armcore import scanners  # noqa: E402
from armcore import mrz as mrz_parser  # noqa: E402


def _hr(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main(argv) -> int:
    out_dir = os.path.join(_HERE, "regula_selftest_out")
    image = argv[1] if len(argv) > 1 else None

    _hr("1. Подключение к COM-объекту Regula")
    print(f"ProgID        = {scanners.RegulaScanner.PROGID}")
    print(f"Папка вывода  = {out_dir}")
    print(f"Режим         = {'из файла: ' + image if image else 'захват с устройства'}")

    scanner = scanners.RegulaScanner()
    try:
        scanner._load_sdk()
        print("OK: COM-объект создан, распознавание включено.")
    except scanners.ScannerError as e:
        print("ОШИБКА подключения к SDK:")
        print(" ", e)
        print("\nПришлите этот вывод разработчику.")
        return 2

    _hr("2. Захват и распознавание")
    try:
        if image:
            cap = scanner.process_image(image, out_dir)
        else:
            print("Положите паспорт на сканер, ожидание до 30 сек...")
            cap = scanner.capture_passport(out_dir, timeout_s=30)
    except scanners.ScannerError as e:
        print("ОШИБКА захвата/распознавания:")
        print(" ", e)
        print("\nПришлите этот вывод разработчику.")
        return 3

    _hr("3. Результат")
    print("MRZ:")
    print(cap.mrz_text or "  (пусто)")
    print("\nПоля визуальной зоны (VIZ):")
    for k, v in (cap.viz_fields or {}).items():
        print(f"  {k}: {v}")
    if not cap.viz_fields:
        print("  (пусто)")
    print("\nСохранённые изображения:")
    for p in cap.image_paths or []:
        print(f"  {p}")
    if not cap.image_paths:
        print("  (нет — портрет не получен)")

    if cap.mrz_text:
        res = mrz_parser.parse(cap.mrz_text)
        _hr("4. Разбор MRZ парсером программы")
        if res:
            for k, v in res.to_fields().items():
                print(f"  {k}: {v}")
            print(f"\n  Контрольные цифры сошлись: {res.valid}")
        else:
            print("  Не удалось разобрать MRZ.")

    _hr("ГОТОВО")
    print(f"Скопируйте вывод выше и содержимое папки:\n  {out_dir}\nи пришлите разработчику.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
