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
            print("Положите паспорт на сканер, ожидание до 60 сек...")
            cap = scanner.capture_passport(out_dir, timeout_s=60)
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

    _hr("3a. Диагностика портрета (графики)")
    try:
        print(" ", scanner.graphics_info())
    except Exception as e:  # noqa: BLE001
        print("  ошибка:", e)

    _hr("3b. ВСЕ текстовые поля, которые отдал SDK (диагностика)")
    try:
        all_fields = scanner.dump_text_fields()
        if all_fields:
            for k, v in all_fields.items():
                print(f"  {k}: {v}")
        else:
            print("  (SDK не вернул ни одного текстового поля)")
    except Exception as e:  # noqa: BLE001
        print("  ошибка дампа:", e)

    if cap.mrz_text:
        res = mrz_parser.parse(cap.mrz_text)
        _hr("4. Разбор MRZ парсером программы")
        if res:
            for k, v in res.to_fields().items():
                print(f"  {k}: {v}")
            print(f"\n  Контрольные цифры сошлись: {res.valid}")
        else:
            print("  Не удалось разобрать MRZ.")

    # Полная диагностика — пишем в файл и открываем его (копировать не нужно).
    lines = []
    lines.append("=== REGULA SELFTEST DIAG ===")
    try:
        lines.append(f"RESULT_TYPES: {scanner.diagnostics()}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"RESULT_TYPES: ошибка: {e}")
    try:
        lines.append(f"PORTRAIT_DIAG: {scanner.graphics_info()}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"PORTRAIT_DIAG: ошибка: {e}")
    try:
        fields = scanner.dump_text_fields()
        lines.append(f"FIELDS_DIAG: {fields if fields else '(пусто)'}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"FIELDS_DIAG: ошибка: {e}")
    lines.append(f"MRZ: {cap.mrz_text!r}")
    lines.append(f"VIZ: {cap.viz_fields}")
    lines.append(f"IMAGES: {cap.image_paths}")
    lines.append(f"OUT_DIR: {out_dir}")
    try:
        xml = scanner.lexical_xml()
        lines.append("--- OCRLexicalAnalyze XML (первые 4000 симв.) ---")
        lines.append(xml[:4000] if xml else "(пусто)")
    except Exception as e:  # noqa: BLE001
        lines.append(f"LEXICAL_XML ошибка: {e}")

    os.makedirs(out_dir, exist_ok=True)
    diag_path = os.path.join(out_dir, "diag.txt")
    with open(diag_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    _hr("ГОТОВО")
    print("\n".join(lines))
    print(f"\nДиагностика сохранена в файл:\n  {diag_path}")
    print("Открываю файл — пришлите его разработчику.")
    try:
        os.startfile(diag_path)  # type: ignore[attr-defined]  (Windows)
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
