#!/usr/bin/env python3
"""
32-битный помощник сканирования документа через TWAIN.

Запускается ОТДЕЛЬНЫМ 32-битным Python (py -3-32), потому что TWAIN-драйверы
сканеров документов (Kodak SceyeX и др.) обычно 32-битные и не видны из
64-битного процесса. Основная программа (64-бит, PyQt6 + Regula) вызывает этот
скрипт через subprocess.

Использование:
    py -3-32 tools/twain_scan.py <папка_вывода>

Сохраняет страницы как doc_00.bmp, doc_01.bmp, ... в папку вывода и печатает
"PAGES=<n>". Требует только пакет pytwain (pip install pytwain) в 32-бит Python.
Конвертацию BMP->JPG делает основная программа (там есть Pillow).
"""
import glob
import os
import sys


def find_dsm(twain_module):
    """Найти TWAINDSM.dll: env ARM_TWAINDSM -> пакет pytwain -> System32/SysWOW64."""
    env = os.environ.get("ARM_TWAINDSM")
    if env and os.path.exists(env):
        return env
    cands = []
    try:
        pkg = os.path.dirname(os.path.abspath(twain_module.__file__))
        cands += glob.glob(os.path.join(pkg, "**", "*twaindsm*.dll"), recursive=True)
        cands += glob.glob(os.path.join(pkg, "**", "TWAINDSM.dll"), recursive=True)
    except Exception:
        pass
    win = os.environ.get("WINDIR", r"C:\Windows")
    # Для 32-битного процесса системные 32-бит DLL лежат в SysWOW64.
    cands += [os.path.join(win, "SysWOW64", "twaindsm.dll"),
              os.path.join(win, "System32", "twaindsm.dll"),
              os.path.join(win, "twaindsm.dll"),
              os.path.join(win, "twain_32.dll")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def main():
    if len(sys.argv) < 2:
        print("ERROR: не задана папка вывода")
        return 2
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)

    try:
        import twain
    except ImportError:
        print("ERROR: не установлен pytwain в 32-битном Python "
              "(py -3-32 -m pip install pytwain)")
        return 3

    dsm = find_dsm(twain)
    sm = None
    src = None
    try:
        try:
            sm = twain.SourceManager(0, dsm_name=dsm) if dsm else twain.SourceManager(0)
        except Exception as e:
            print(f"ERROR: менеджер TWAIN (DSM): {e}. DSM={dsm}")
            return 4
        src = sm.open_source()
        if src is None:
            print("ERROR: сканер не выбран")
            return 5
        for cap, ctype, val in (
            ("ICAP_PIXELTYPE", "TWTY_UINT16", "TWPT_RGB"),
            ("ICAP_XRESOLUTION", "TWTY_FIX32", 300.0),
            ("ICAP_YRESOLUTION", "TWTY_FIX32", 300.0),
        ):
            try:
                src.set_capability(getattr(twain, cap), getattr(twain, ctype),
                                   getattr(twain, val) if isinstance(val, str) else val)
            except Exception:
                pass

        try:
            src.request_acquire(show_ui=True, modal_ui=True)
        except Exception as e:
            print(f"ERROR: request_acquire: {type(e).__name__}: {e}")
            return 7
        idx = 0
        while True:
            try:
                rv = src.xfer_image_natively()
            except Exception as e:
                if idx == 0:
                    print(f"INFO: передача прервана: {type(e).__name__}: {e}")
                break
            if not rv:
                if idx == 0:
                    print("INFO: сканер не вернул изображение (нет данных).")
                break
            handle = rv[0] if isinstance(rv, tuple) else rv
            path = os.path.join(out_dir, f"doc_{idx:02d}.bmp")
            try:
                twain.dib_to_bm_file(handle, path)
            finally:
                try:
                    twain.global_handle_free(handle)
                except Exception:
                    pass
            idx += 1
            more = rv[1] if isinstance(rv, tuple) and len(rv) > 1 else 0
            if not more:
                break
        print(f"PAGES={idx}")
        return 0
    except Exception as e:
        print(f"ERROR: {e}")
        return 6
    finally:
        for obj in (src, sm):
            try:
                if obj is not None:
                    obj.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
