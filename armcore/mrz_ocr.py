"""
Распознавание MRZ паспорта из готового изображения (OCR).

Позволяет вытащить данные паспорта не с физического сканера, а из обычного
скан-файла (jpg/png/...), выбранного оператором с диска. Используется
библиотека passporteye (детекция MRZ-зоны + tesseract), с запасным вариантом
прямого OCR через pytesseract.

Сканы часто бывают повёрнуты (MRZ-зона идёт сбоку) и в низком разрешении,
поэтому функция перебирает повороты страницы и при необходимости апскейлит
изображение, выбирая тот вариант, который успешно разбирается парсером
``armcore.mrz.parse`` (предпочитая результат со сошедшимися контрольными
цифрами).

Возвращает СЫРОЙ текст MRZ (2 строки), который дальше разбирается общим
парсером ``armcore.mrz.parse`` — так контрольные цифры и поля считаются в одном
месте.
"""
from __future__ import annotations

import os
import shutil
from typing import List, Optional, Tuple

# Повороты в порядке вероятности: 0° (норма), 90°/270° (скан «боком»), 180°.
_ROTATIONS = (0, 90, 270, 180)
# Если меньшая сторона скана меньше этого порога — апскейлим перед OCR.
_MIN_SIDE = 1500
_TESS_CFG = ("--psm 6 -c tessedit_char_whitelist="
             "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<")

# Стандартные пути установки Tesseract OCR на Windows (UB Mannheim installer).
_WIN_TESSERACT_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _find_tesseract() -> Optional[str]:
    """Найти исполняемый файл tesseract: env ARM_TESSERACT -> PATH -> станд. пути."""
    env = os.environ.get("ARM_TESSERACT")
    if env and os.path.exists(env):
        return env
    found = shutil.which("tesseract")
    if found:
        return found
    for p in _WIN_TESSERACT_PATHS:
        if os.path.exists(p):
            return p
    # На Windows tesseract может быть в %LOCALAPPDATA%\Programs\Tesseract-OCR
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cand = os.path.join(local, "Programs", "Tesseract-OCR", "tesseract.exe")
        if os.path.exists(cand):
            return cand
    return None


def _configure_tesseract() -> Optional[str]:
    """Прописать путь к tesseract в pytesseract (важно для Windows). Вернёт путь."""
    exe = _find_tesseract()
    if not exe:
        return None
    try:
        import pytesseract  # type: ignore
        pytesseract.pytesseract.tesseract_cmd = exe
    except ImportError:
        pass
    return exe


def ocr_diagnostics() -> dict:
    """Состояние OCR-окружения: что установлено, что нет (для подсказок оператору)."""
    info = {"tesseract": _find_tesseract(), "passporteye": False,
            "pytesseract": False, "pillow": False}
    for mod in ("passporteye", "pytesseract", "PIL"):
        try:
            __import__(mod)
            info["pillow" if mod == "PIL" else mod] = True
        except ImportError:
            pass
    return info


def mrz_text_from_image(image_path: str) -> Optional[str]:
    """Вернуть текст MRZ (2 строки) из изображения паспорта или None.

    Перебирает повороты и масштаб, оценивает каждый разобранный кандидат и
    возвращает лучший. Скоринг важнее простого флага ``valid``: на шумных
    сканах перевёрнутая «каша» иногда формально проходит проверку контрольных
    цифр (в их позициях оказываются буквы — проверка пропускается), поэтому
    мы дополнительно требуем правдоподобные поля (фамилия-латиница, код страны,
    даты).
    """
    # Ленивый импорт, чтобы избежать циклической зависимости mrz <-> mrz_ocr.
    from . import mrz as mrz_parser

    # На Windows tesseract обычно не в PATH — прописываем путь явно.
    _configure_tesseract()

    best_text: Optional[str] = None
    best_score = 0
    for text in _candidates(image_path):
        result = mrz_parser.parse(text)
        if not result:
            continue
        score = _score(result)
        if score > best_score:
            best_score, best_text = score, text
    # Порог отсекает мусор (перевёрнутые/зеркальные чтения).
    return best_text if best_score >= 3 else None


def _score(result) -> int:
    """Оценка правдоподобности разобранной MRZ (чем больше — тем надёжнее)."""
    score = 0
    family = (result.family_latin or "").replace(" ", "")
    given = (result.given_latin or "").replace(" ", "")
    # Фамилия должна выглядеть как имя латиницей разумной длины.
    if family.isalpha() and 2 <= len(family) <= 20:
        score += 2
    elif len(family) > 24:
        score -= 3                       # один длинный «токен-каша» — признак мусора
    if given.isalpha() and 1 <= len(given) <= 20:
        score += 1
    cc = result.country_code or ""
    if len(cc) == 3 and cc.isalpha():
        score += 1
    if result.passport_number:
        score += 1
    if result.birth_date:
        score += 1
    if result.expiry_date:
        score += 1
    if result.valid and result.birth_date and result.expiry_date:
        score += 2                       # полностью чистое чтение
    return score


def _candidates(image_path: str) -> List[str]:
    """Все найденные варианты MRZ-текста (passporteye + pytesseract, все повороты)."""
    texts: List[str] = []
    images = _load_variants(image_path)
    for img in images:
        t = _passporteye_text(img)
        if t:
            texts.append(t)
    for img in images:
        t = _pytesseract_text(img)
        if t:
            texts.append(t)
    return texts


def _load_variants(image_path: str):
    """Загрузить изображение и вернуть его повёрнутые/масштабированные версии."""
    try:
        from PIL import Image
    except ImportError:
        return []
    try:
        base = Image.open(image_path).convert("L")
    except Exception:  # noqa: BLE001
        return []
    # Апскейл мелких сканов — заметно повышает качество OCR цифр.
    w, h = base.size
    if min(w, h) < _MIN_SIDE:
        factor = max(2, round(_MIN_SIDE / max(1, min(w, h))))
        base = base.resize((w * factor, h * factor), Image.LANCZOS)
    return [base.rotate(deg, expand=True) for deg in _ROTATIONS]


def _passporteye_text(img) -> Optional[str]:
    """Прогнать готовое (повёрнутое) изображение через passporteye."""
    try:
        import tempfile
        import warnings
        from passporteye import read_mrz  # type: ignore
    except ImportError:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
            img.save(tmp.name)
            with warnings.catch_warnings():
                # passporteye тянет устаревшие API skimage — не засоряем консоль.
                warnings.simplefilter("ignore")
                mrz = read_mrz(tmp.name)
    except Exception:  # noqa: BLE001 — любая ошибка OCR не должна ронять GUI
        return None
    if mrz is None:
        return None
    raw = mrz.to_dict().get("raw_text")
    if raw and "\n" in raw:
        return raw
    return None


def _pytesseract_text(img) -> Optional[str]:
    """Запасной OCR: прогнать всю (повёрнутую) страницу через tesseract."""
    try:
        import pytesseract  # type: ignore
    except ImportError:
        return None
    try:
        text = pytesseract.image_to_string(img, config=_TESS_CFG)
    except Exception:  # noqa: BLE001
        return None
    lines = [ln.strip().replace(" ", "") for ln in text.splitlines() if ln.strip()]
    long_lines = [ln for ln in lines if len(ln) >= 30]
    if len(long_lines) >= 2:
        return "\n".join(long_lines[-2:])
    return None
