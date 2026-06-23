"""
Подбор шрифтов для отрисовки текста в PDF/изображениях.

Чтобы кириллица в подписях к разворотам и любых надписях на листах
отображалась чётко на Windows (а не «квадратиками»), нужен TrueType-шрифт с
поддержкой кириллицы. Модуль ищет системный шрифт по типичным путям Windows,
затем macOS/Linux, затем — фолбэки PIL.
"""
from __future__ import annotations

import os
from functools import lru_cache

from PIL import ImageFont

# Кандидаты по приоритету. Windows-шрифты идут первыми (целевая ОС).
_FONT_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    r"C:\Windows\Fonts\times.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

# Имена для поиска средствами самой PIL (она просматривает системные папки).
_FONT_NAMES = ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"]


@lru_cache(maxsize=32)
def load_cyrillic_font(size: int = 48) -> ImageFont.FreeTypeFont:
    """Вернуть TrueType-шрифт с кириллицей нужного размера (с фолбэками)."""
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    for name in _FONT_NAMES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    # Последний фолбэк: встроенный шрифт PIL (в новых версиях — DejaVu).
    try:
        return ImageFont.load_default(size)  # Pillow >= 10
    except TypeError:
        return ImageFont.load_default()
