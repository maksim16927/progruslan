"""
Формирование разворотов и сборка PDF.

ТЗ, п.3.1(5) и п.5.2:
  «Из всех имеющихся сканов автоматически формируются PDF-листы. Важно:
  перевод паспорта и паспорт — 4 страницы на развороте (2 полных разворота
  паспорта на одной странице). На лист А4 альбомной ориентации размещается
  4 изображения (сетка 2x2) с сохранением исходного качества 300 dpi.
  Сохранение как «паспорт.pdf».»

Реализация на Pillow (без внешних бинарников): изображения масштабируются под
ячейку 2x2 с сохранением пропорций, страницы пишутся в многостраничный PDF с
разрешением 300 dpi.
"""
from __future__ import annotations

import os
from typing import List, Sequence, Tuple

from PIL import Image, ImageDraw

from .config import SCAN_DPI, A4_LANDSCAPE_PX, A4_PORTRAIT_PX
from .fonts import load_cyrillic_font

# Высота полосы под подпись сверху листа (в пикселях при 300 dpi).
CAPTION_HEIGHT = 110

_IMG_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")


def list_images(folder: str) -> List[str]:
    """Изображения из папки, упорядоченные по времени создания (как сканировали)."""
    if not os.path.isdir(folder):
        return []
    files = [
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.lower().endswith(_IMG_EXT)
    ]
    files.sort(key=os.path.getctime)
    return files


def _fit_into(img: Image.Image, box_w: int, box_h: int,
              auto_rotate: bool = False) -> Image.Image:
    """Вписать изображение в ячейку с сохранением пропорций (без обрезки).

    auto_rotate — повернуть на 90°, если ориентация снимка не совпадает с
    ориентацией ячейки (альбомный разворот в книжную ячейку и наоборот):
    так изображение занимает ячейку целиком, а не полоску посередине.
    """
    img = img.convert("RGB")
    w, h = img.size
    if auto_rotate and ((w > h) != (box_w > box_h)):
        img = img.rotate(90, expand=True)
        w, h = img.size
    scale = min(box_w / w, box_h / h)
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return img.resize(new_size, Image.LANCZOS)


def _draw_caption(page: Image.Image, text: str, top: int, height: int):
    """Нарисовать подпись (кириллица) по центру полосы высотой height."""
    draw = ImageDraw.Draw(page)
    font = load_cyrillic_font(size=max(28, height - 56))
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (page.width - tw) // 2 - bbox[0]
    y = top + (height - th) // 2 - bbox[1]
    draw.text((x, y), text, fill=(20, 20, 20), font=font)


def compose_grid_page(image_paths: Sequence[str],
                      page_px: Tuple[int, int] = A4_LANDSCAPE_PX,
                      grid: Tuple[int, int] = (2, 2),
                      margin: int = 40,
                      gutter: int = 30,
                      caption: str | None = None,
                      auto_rotate: bool = False) -> Image.Image:
    """Собрать один лист: до cols*rows изображений в сетке grid (cols, rows).

    Если задан caption — сверху рисуется подпись кириллическим шрифтом.
    """
    page_w, page_h = page_px
    cols, rows = grid
    page = Image.new("RGB", (page_w, page_h), (255, 255, 255))

    header_h = CAPTION_HEIGHT if caption else 0
    if caption:
        _draw_caption(page, caption, top=margin // 2, height=header_h)

    grid_top = margin + header_h
    cell_w = (page_w - 2 * margin - (cols - 1) * gutter) // cols
    cell_h = (page_h - margin - grid_top - (rows - 1) * gutter) // rows

    for idx, path in enumerate(image_paths[: cols * rows]):
        try:
            src = Image.open(path)
        except (OSError, ValueError):
            continue
        thumb = _fit_into(src, cell_w, cell_h, auto_rotate=auto_rotate)
        r, c = divmod(idx, cols)
        cell_x = margin + c * (cell_w + gutter)
        cell_y = grid_top + r * (cell_h + gutter)
        # Центрирование внутри ячейки.
        off_x = cell_x + (cell_w - thumb.width) // 2
        off_y = cell_y + (cell_h - thumb.height) // 2
        page.paste(thumb, (off_x, off_y))
    return page


def make_spreads_pdf(image_paths: Sequence[str], output_pdf: str,
                     per_sheet: int = 4, grid: Tuple[int, int] = (2, 2),
                     landscape: bool = False, caption: str | None = None) -> str:
    """Сформировать PDF разворотов: по per_sheet изображений на лист (2x2).

    caption — подпись (например, «ФИО — Паспорт»), печатается сверху каждого
    листа кириллическим шрифтом. Сохраняет с разрешением 300 dpi.
    """
    if not image_paths:
        raise ValueError("Нет изображений для формирования разворотов")

    page_px = A4_LANDSCAPE_PX if landscape else A4_PORTRAIT_PX
    total = (len(image_paths) + per_sheet - 1) // per_sheet
    pages: List[Image.Image] = []
    for idx, i in enumerate(range(0, len(image_paths), per_sheet), start=1):
        chunk = image_paths[i: i + per_sheet]
        page_caption = f"{caption} — лист {idx}/{total}" if caption else None
        pages.append(compose_grid_page(chunk, page_px=page_px, grid=grid,
                                       caption=page_caption, auto_rotate=True))

    os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
    first, rest = pages[0], pages[1:]
    first.save(
        output_pdf, "PDF", resolution=float(SCAN_DPI),
        save_all=True, append_images=rest,
    )
    return output_pdf


def images_to_pdf(image_paths: Sequence[str], output_pdf: str,
                  page_px: Tuple[int, int] = A4_PORTRAIT_PX) -> str:
    """Простая сборка: каждое изображение — отдельная страница A4, 300 dpi.

    Используется для «сырых» сканов и сшитых документов (Kodak).
    """
    if not image_paths:
        raise ValueError("Нет изображений для сборки PDF")
    pages = [compose_grid_page([p], page_px=page_px, grid=(1, 1), margin=0, gutter=0)
             for p in image_paths]
    os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
    pages[0].save(
        output_pdf, "PDF", resolution=float(SCAN_DPI),
        save_all=True, append_images=pages[1:],
    )
    return output_pdf
