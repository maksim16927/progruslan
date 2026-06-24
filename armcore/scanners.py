"""
Слой работы со сканерами: Regula 7017 (паспорта) и Kodak SceyeX (документы).

ТЗ, п.2 и п.5.1:
  * Regula 7017 — SDK или TWAIN-драйвер; захват УФ/белый/ИК, чтение MRZ и VIZ,
    режим booksheet (постранично/книжный разворот), 300 dpi.
  * Kodak SceyeX — TWAIN/WIA/файловый захват; многостраничный документ, 300 dpi.
  * Все сканы: 300 dpi, цветной PDF.

Здесь — единый интерфейс и КАРКАС под реальный SDK (как выбрано в задании),
плюс MockScanner для разработки/тестов без оборудования.

Реальные вызовы SDK помечены TODO и изолированы в RegulaScanner/KodakScanner,
чтобы при появлении железа дорабатывался только один файл.
"""
from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from PIL import Image

from .config import Config, SCAN_DPI


class ScannerError(RuntimeError):
    """Ошибка работы со сканером (нет драйвера, нет устройства, сбой захвата)."""


@dataclass
class PassportCapture:
    """Результат захвата паспорта на Regula."""
    image_paths: List[str] = field(default_factory=list)  # сканы (главная стр. и т.д.)
    mrz_text: str = ""                                    # сырой текст MRZ (2 строки)
    viz_fields: dict = field(default_factory=dict)        # данные визуальной зоны (OCR)


# --------------------------------------------------------------------------- #
#  Базовый интерфейс
# --------------------------------------------------------------------------- #
class BaseScanner(ABC):
    name = "scanner"
    dpi = SCAN_DPI

    @abstractmethod
    def is_available(self) -> bool:
        """Доступно ли устройство/драйвер."""

    def capture_passport(self, out_dir: str) -> PassportCapture:
        raise ScannerError(f"{self.name}: захват паспорта не поддерживается")

    def scan_pages(self, out_dir: str, max_pages: Optional[int] = None) -> List[str]:
        """Постраничное сканирование (режим booksheet Regula)."""
        raise ScannerError(f"{self.name}: постраничное сканирование не поддерживается")

    def scan_document(self, out_dir: str) -> List[str]:
        """Многостраничный сшитый документ (Kodak)."""
        raise ScannerError(f"{self.name}: сканирование документа не поддерживается")


def _save_at_dpi(img: Image.Image, path: str, dpi: int = SCAN_DPI) -> str:
    """Сохранить изображение цветным с проставленным разрешением 300 dpi."""
    img = img.convert("RGB")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path, dpi=(dpi, dpi), quality=95)
    return path


# --------------------------------------------------------------------------- #
#  Regula 7017 — каркас под реальный SDK
# --------------------------------------------------------------------------- #
class RegulaScanner(BaseScanner):
    """Сканер паспортов Regula 7017 через Regula Document Reader **Desktop SDK**.

    Desktop SDK (Windows, .dll + Python-обёртка из поставки Regula) сам управляет
    устройством и выполняет распознавание: захват кадра (белый/УФ/ИК, 300 dpi),
    чтение MRZ и визуальной зоны (VIZ), извлечение портрета. Программа получает
    уже готовые поля — это и есть «зашитый модуль распознавания».

    ВНИМАНИЕ: точные имена классов/полей Python-обёртки различаются между версиями
    SDK. Здесь интеграция изолирована в одном классе и опирается на типичный API
    (DocumentReader / recognize / Text.get_field / Graphics). Перед боевым
    запуском сверить вызовы с версией SDK на машине заказчика, прогнав
    ``tools/regula_selftest.py``.
    """
    name = "Regula 7017"

    def __init__(self, dll_path: Optional[str] = None,
                 license_path: Optional[str] = None):
        self.dll_path = dll_path
        self.license_path = license_path
        self._sdk = None

    # ------------------------------------------------------------------ SDK
    def _load_sdk(self):
        if self._sdk is not None:
            return self._sdk
        try:
            # Desktop-обёртка Regula Document Reader SDK.
            from regula.documentreader.api import DocumentReader  # type: ignore
        except ImportError as e:
            raise ScannerError(
                "Не найден Regula Document Reader Desktop SDK (Python-обёртка). "
                "Установите SDK из поставки Regula (вместе с .dll) и пакет "
                "regula.documentreader.api."
            ) from e

        license_data = None
        if self.license_path:
            if not os.path.exists(self.license_path):
                raise ScannerError(f"Лицензия Regula не найдена: {self.license_path}")
            with open(self.license_path, "rb") as fh:
                license_data = fh.read()
        try:
            # Инициализация рантайма SDK с лицензией; путь к .dll задаётся
            # переменной окружения/поставкой SDK.
            reader = DocumentReader()
            reader.initialize_reader(license_data) if license_data else \
                reader.initialize_reader()
        except Exception as e:  # noqa: BLE001
            raise ScannerError(
                f"Не удалось инициализировать Regula SDK: {e}. "
                "Проверьте .dll (ARM_REGULA_DLL) и лицензию (ARM_REGULA_LICENSE)."
            ) from e
        self._sdk = reader
        return self._sdk

    def is_available(self) -> bool:
        try:
            self._load_sdk()
            return True
        except ScannerError:
            return False

    # -------------------------------------------------------------- захват
    def capture_passport(self, out_dir: str) -> PassportCapture:
        reader = self._load_sdk()
        # Захват кадра с устройства Regula 7017 (белый/УФ/ИК, 300 dpi) и
        # распознавание встроенным модулем SDK -> готовые MRZ и VIZ.
        try:
            response = self._recognize_from_device(reader)
        except ScannerError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ScannerError(f"Regula: ошибка захвата/распознавания: {e}") from e

        mrz_text = self._field(response, "mrz_strings") or self._field(response, "mrz")
        viz = self._extract_viz(response)
        images = self._save_response_images(response, out_dir)
        return PassportCapture(image_paths=images, mrz_text=mrz_text or "",
                               viz_fields=viz)

    def _recognize_from_device(self, reader):
        """Захватить кадр с устройства и распознать.

        Реализация зависит от версии Desktop SDK. Типовой путь: получить кадр
        со сканера (reader.scan/grab) и передать в reader.recognize(...) со
        сценарием FullProcess. Точные имена сверяются self-test'ом.
        """
        if not hasattr(reader, "recognize"):
            raise ScannerError(
                "Версия Regula SDK не поддерживает ожидаемый API recognize(); "
                "сверьте вызовы через tools/regula_selftest.py."
            )
        # Захват изображения с устройства (метод зависит от версии SDK).
        if hasattr(reader, "scan"):
            image = reader.scan()
            return reader.recognize(image)
        # Некоторые версии совмещают захват и распознавание в одном вызове.
        return reader.recognize()

    # ------------------------------------------------------------- разбор
    @staticmethod
    def _field(response, name: str) -> str:
        """Достать текстовое поле из ответа SDK по логическому имени (best-effort)."""
        try:
            text = getattr(response, "text", None)
            if text is None:
                return ""
            getter = getattr(text, "get_field_value", None)
            if getter:
                return getter(name) or ""
            return getattr(text, name, "") or ""
        except Exception:  # noqa: BLE001
            return ""

    def _extract_viz(self, response) -> dict:
        """Поля визуальной зоны (нет в MRZ) -> ключи полей GUI."""
        viz = {
            "PATRONYMIC": self._field(response, "middle_name"),
            "BIRTHPLACE": self._field(response, "place_of_birth"),
            "DATE_ISSUE": self._field(response, "date_of_issue"),
            "ISSUED_BY": self._field(response, "authority"),
        }
        return {k: v for k, v in viz.items() if v}

    @staticmethod
    def _save_response_images(response, out_dir: str) -> List[str]:
        """Сохранить снимок паспорта и портрет владельца из ответа SDK."""
        os.makedirs(out_dir, exist_ok=True)
        saved: List[str] = []
        graphics = getattr(response, "graphics", None)
        # Снимок документа.
        for attr, fname in (("document_image", "passport_00.jpg"),
                            ("portrait", "portrait.jpg")):
            try:
                getter = getattr(graphics, "get_field_image", None) if graphics else None
                data = getter(attr) if getter else None
                if data:
                    path = os.path.join(out_dir, fname)
                    with open(path, "wb") as fh:
                        fh.write(data)
                    saved.append(path)
            except Exception:  # noqa: BLE001 — отсутствие поля не критично
                pass
        return saved

    def scan_pages(self, out_dir: str, max_pages: Optional[int] = None) -> List[str]:
        reader = self._load_sdk()
        # Режим booksheet — постранично со сканера при 300 dpi (ТЗ, п.3.1(3), 5.1).
        if not hasattr(reader, "scan_pages"):
            raise ScannerError(
                "Версия Regula SDK не поддерживает постраничный захват; "
                "сверьте вызовы через tools/regula_selftest.py."
            )
        os.makedirs(out_dir, exist_ok=True)
        paths = reader.scan_pages(out_dir, max_pages)  # зависит от версии SDK
        return list(paths)[:max_pages] if max_pages else list(paths)


# --------------------------------------------------------------------------- #
#  Kodak SceyeX — каркас под TWAIN/WIA
# --------------------------------------------------------------------------- #
class KodakScanner(BaseScanner):
    """Книжный сканер Kodak SceyeX — многостраничные сшитые документы.

    Доступ через TWAIN (pyinsane2 / модуль twain) или WIA. Ниже — точки
    интеграции, помеченные TODO.
    """
    name = "Kodak SceyeX"

    def __init__(self, device_name: Optional[str] = None):
        self.device_name = device_name

    def _open_twain(self):
        # TODO(TWAIN): открыть устройство.
        #   import pyinsane2
        #   pyinsane2.init()
        #   devices = pyinsane2.get_devices()
        #   dev = <выбор по self.device_name>
        #   pyinsane2.set_scanner_opt(dev, 'resolution', [self.dpi])
        #   pyinsane2.set_scanner_opt(dev, 'mode', ['Color'])
        raise ScannerError(
            "TWAIN-драйвер Kodak не подключён. Реализуйте KodakScanner._open_twain "
            "через pyinsane2/twain (mode=Color, resolution=300)."
        )

    def is_available(self) -> bool:
        try:
            self._open_twain()
            return True
        except ScannerError:
            return False

    def scan_document(self, out_dir: str) -> List[str]:
        self._open_twain()
        # TODO(TWAIN): отсканировать все листы как многостраничный документ при
        #   300 dpi (цвет), сохранить страницы в out_dir, вернуть список путей.
        raise ScannerError("scan_document: требуется реализация под Kodak/TWAIN")


# --------------------------------------------------------------------------- #
#  Mock — имитация без оборудования (разработка/тесты)
# --------------------------------------------------------------------------- #
class MockScanner(BaseScanner):
    """Имитация сканера: берёт готовые изображения из папки-источника.

    Структура источника (рядом с программой, папка mock_scans/):
        mock_scans/passport/   — изображения паспорта (главная страница и т.д.)
        mock_scans/passport/mrz.txt — текст MRZ (2 строки), опционально
        mock_scans/pages/      — все страницы паспорта (booksheet)
        mock_scans/document/   — сшитый многостраничный документ (Kodak)
    Если источника нет — генерирует пустые цветные листы-заглушки.
    """
    name = "MockScanner"

    def __init__(self, source_dir: str):
        self.source_dir = source_dir

    def is_available(self) -> bool:
        return True

    def _copy_from(self, sub: str, out_dir: str, fallback_count: int = 1) -> List[str]:
        os.makedirs(out_dir, exist_ok=True)
        src = os.path.join(self.source_dir, sub)
        paths: List[str] = []
        if os.path.isdir(src):
            files = sorted(
                f for f in os.listdir(src)
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"))
            )
            for i, f in enumerate(files):
                img = Image.open(os.path.join(src, f))
                dst = os.path.join(out_dir, f"scan_{i:02d}.jpg")
                _save_at_dpi(img, dst, self.dpi)
                paths.append(dst)
        if not paths:  # источника нет — заглушки
            for i in range(fallback_count):
                img = Image.new("RGB", (1654, 2339), (245, 245, 245))
                dst = os.path.join(out_dir, f"scan_{i:02d}.jpg")
                _save_at_dpi(img, dst, self.dpi)
                paths.append(dst)
        return paths

    def capture_passport(self, out_dir: str) -> PassportCapture:
        images = self._copy_from("passport", out_dir, fallback_count=1)
        mrz_text = ""
        mrz_file = os.path.join(self.source_dir, "passport", "mrz.txt")
        if os.path.exists(mrz_file):
            with open(mrz_file, "r", encoding="utf-8") as f:
                mrz_text = f.read()
        # Если готового текста MRZ нет — пытаемся распознать его прямо из скана,
        # чтобы кнопка «Считать паспорт» и в mock-режиме заполняла поля.
        if not mrz_text and images:
            try:
                from . import mrz_ocr
                mrz_text = mrz_ocr.mrz_text_from_image(images[0]) or ""
            except Exception:  # noqa: BLE001 — OCR не должен ронять mock-захват
                mrz_text = ""
        return PassportCapture(image_paths=images, mrz_text=mrz_text)

    def scan_pages(self, out_dir: str, max_pages: Optional[int] = None) -> List[str]:
        pages = self._copy_from("pages", out_dir, fallback_count=4)
        return pages[:max_pages] if max_pages else pages

    def scan_document(self, out_dir: str) -> List[str]:
        return self._copy_from("document", out_dir, fallback_count=3)


# --------------------------------------------------------------------------- #
#  Фабрики
# --------------------------------------------------------------------------- #
def get_passport_scanner(cfg: Config) -> BaseScanner:
    """Сканер паспортов: Mock или Regula в зависимости от конфигурации."""
    if cfg.mock_scanners:
        return MockScanner(_mock_dir())
    return RegulaScanner(dll_path=cfg.regula_dll_path or None,
                         license_path=cfg.regula_license_path or None)


def get_document_scanner(cfg: Config) -> BaseScanner:
    """Сканер документов: Mock или Kodak в зависимости от конфигурации."""
    if cfg.mock_scanners:
        return MockScanner(_mock_dir())
    return KodakScanner()


def _mock_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "mock_scans")
