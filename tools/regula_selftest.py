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
    import datetime
    import shutil
    out_dir = os.path.join(_HERE, "regula_selftest_out")
    image = argv[1] if len(argv) > 1 else None

    # Чистим папку вывода — чтобы старые фото/диагностика не путались с новыми.
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    run_stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    _hr("1. Подключение к COM-объекту Regula")
    print(f"ВРЕМЯ ЗАПУСКА = {run_stamp}")
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
    cap = None
    capture_error = ""
    try:
        if image:
            cap = scanner.process_image(image, out_dir)
        else:
            print("Положите паспорт на сканер, ожидание до 60 сек...")
            cap = scanner.capture_passport(out_dir, timeout_s=60)
    except scanners.ScannerError as e:
        capture_error = str(e)
        print("Захват не завершился штатно — соберу диагностику всё равно.")
        print(" ", capture_error)

    # Полная диагностика — пишем в файл и открываем (даже при таймауте захвата).
    lines = []
    lines.append(f"=== REGULA SELFTEST DIAG ({run_stamp}) ===")
    if capture_error:
        lines.append(f"CAPTURE_ERROR: {capture_error}")
    try:
        lines.append(f"FLAGS_DIAG: {scanner.flags_info()}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"FLAGS_DIAG: ошибка: {e}")
    try:
        lines.append(f"RESULT_TYPES: {scanner.diagnostics()}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"RESULT_TYPES: ошибка: {e}")
    try:
        lines.append(f"PORTRAIT_DIAG: {scanner.graphics_info()}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"PORTRAIT_DIAG: ошибка: {e}")
    try:
        lines.append(f"IMAGES_DIAG: {scanner.images_info()}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"IMAGES_DIAG: ошибка: {e}")
    try:
        fields = scanner.dump_text_fields()
        lines.append(f"FIELDS_DIAG: {fields if fields else '(пусто)'}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"FIELDS_DIAG: ошибка: {e}")
    try:
        allc = scanner.dump_all_codes()
        lines.append(f"ALL_CODES: {allc if allc else '(пусто)'}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"ALL_CODES: ошибка: {e}")
    lines.append("MRZ: " + (repr(cap.mrz_text) if cap else "(нет захвата)"))
    lines.append("VIZ: " + (str(cap.viz_fields) if cap else "(нет захвата)"))
    lines.append("IMAGES: " + (str(cap.image_paths) if cap else "(нет захвата)"))
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
    print(f"\nДиагностика сохранена в файл: {diag_path}")

    # Не открываем Блокнот. Если есть скан — открываем картинку.
    scan = os.path.join(out_dir, "passport_scan.jpg")
    if os.path.exists(scan):
        print(f"Скан сохранён: {scan}")
        try:
            os.startfile(scan)  # type: ignore[attr-defined]  (Windows)
        except Exception:  # noqa: BLE001
            pass
    else:
        print("Скан не получен (в этом считывании сканер не отдал изображение).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
