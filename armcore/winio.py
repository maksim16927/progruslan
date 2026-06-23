"""
Платформенно-зависимый ввод-вывод: печать на сетевые принтеры и конвертация
.docx -> .pdf.

ТЗ: целевая ОС — Windows (сетевые принтеры, MS Word). Здесь основная реализация
для Windows (win32print / comtypes), но с аккуратными обёртками и кросс-
платформенными fallback'ами (lpr / LibreOffice), чтобы код импортировался и
частично работал при разработке на macOS/Linux.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess

IS_WINDOWS = platform.system() == "Windows"


# --------------------------------------------------------------------------- #
#  Печать
# --------------------------------------------------------------------------- #
def list_printers() -> list[str]:
    if IS_WINDOWS:
        try:
            import win32print
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            return [p[2] for p in win32print.EnumPrinters(flags)]
        except Exception:
            return []
    # macOS/Linux — через CUPS (lpstat)
    try:
        out = subprocess.run(["lpstat", "-a"], capture_output=True, text=True, timeout=5)
        return [line.split()[0] for line in out.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def default_printer() -> str | None:
    if IS_WINDOWS:
        try:
            import win32print
            return win32print.GetDefaultPrinter()
        except Exception:
            return None
    printers = list_printers()
    return printers[0] if printers else None


def print_file(filepath: str, printer: str | None = None) -> bool:
    """Отправить файл на печать. Возвращает True при успешном запуске задания."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(filepath)
    if IS_WINDOWS:
        import win32api  # type: ignore
        printer = printer or default_printer()
        args = f'/d:"{printer}"' if printer else ""
        win32api.ShellExecute(0, "print", filepath, args or None, ".", 0)
        return True
    # macOS/Linux — lpr
    cmd = ["lpr"]
    if printer:
        cmd += ["-P", printer]
    cmd.append(filepath)
    subprocess.run(cmd, check=True)
    return True


# --------------------------------------------------------------------------- #
#  Конвертация .docx -> .pdf
# --------------------------------------------------------------------------- #
def docx_to_pdf(docx_path: str, pdf_path: str) -> str:
    """Сконвертировать .docx в .pdf. Windows -> MS Word, иначе -> LibreOffice."""
    if IS_WINDOWS:
        import comtypes.client  # type: ignore
        word = comtypes.client.CreateObject("Word.Application")
        word.Visible = False
        try:
            doc = word.Documents.Open(os.path.abspath(docx_path))
            doc.SaveAs(os.path.abspath(pdf_path), FileFormat=17)  # 17 = PDF
            doc.Close()
        finally:
            word.Quit()
        return pdf_path

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError(
            "Конвертация .docx->.pdf вне Windows требует LibreOffice (soffice) в PATH."
        )
    out_dir = os.path.dirname(os.path.abspath(pdf_path))
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir,
         os.path.abspath(docx_path)],
        check=True, capture_output=True, timeout=120,
    )
    produced = os.path.join(out_dir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
    if produced != pdf_path and os.path.exists(produced):
        shutil.move(produced, pdf_path)
    return pdf_path
