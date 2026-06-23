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
    """Сканер паспортов Regula 7017.

    Подключение к реальному устройству выполняется через Regula Document Reader
    SDK (обёртка .dll через ctypes) или через REST API локального сервиса Regula.
    Ниже — точки интеграции, помеченные TODO.
    """
    name = "Regula 7017"

    def __init__(self, sdk_path: Optional[str] = None, rest_url: Optional[str] = None):
        self.sdk_path = sdk_path
        self.rest_url = rest_url
        self._sdk = None

    def _load_sdk(self):
        if self._sdk is not None:
            return self._sdk
        if not self.rest_url:
            raise ScannerError(
                "Regula SDK не подключён. Запустите локальный сервис Regula "
                "Document Reader (Web API) и задайте его адрес в ARM_REGULA_URL "
                "(или regula_rest_url в arm_config.json)."
            )
        try:
            from regula.documentreader.webclient import DocumentReaderApi  # type: ignore
        except ImportError as e:
            raise ScannerError(
                "Не установлен пакет regula.documentreader.webclient. "
                "Установите его (pip install regula.documentreader.webclient)."
            ) from e
        # Клиент Web API; реальная проверка доступности — при первом запросе.
        self._sdk = DocumentReaderApi(host=self.rest_url)
        return self._sdk

    def is_available(self) -> bool:
        try:
            self._load_sdk()
            return True
        except ScannerError:
            return False

    def capture_passport(self, out_dir: str) -> PassportCapture:
        api = self._load_sdk()
        # ТЗ, п.5.1: захват и распознавание выполняет сам сервис Regula —
        # он возвращает готовые MRZ и VIZ (зашитый модуль распознавания).
        try:
            from regula.documentreader.webclient import (  # type: ignore
                RecognitionRequest, Scenario, TextFieldType,
            )
        except ImportError as e:
            raise ScannerError("regula.documentreader.webclient недоступен") from e

        # Захват изображения с устройства/папки. На реальном железе сюда
        # подставляется кадр со сканера Regula 7017; здесь читаем последний
        # сохранённый скан, если он есть.
        image_bytes = self._grab_image_bytes(out_dir)
        try:
            request = RecognitionRequest(
                scenario=Scenario.FULL_PROCESS, images=[image_bytes]
            )
            response = api.process(request)
        except Exception as e:  # noqa: BLE001 — сетевые/SDK-сбои -> понятная ошибка
            raise ScannerError(f"Regula: ошибка распознавания: {e}") from e

        mrz_text = response.text.get_field_value(TextFieldType.MRZ_STRINGS) or ""
        viz = self._extract_viz(response, TextFieldType)
        images = self._save_response_images(response, out_dir) or (
            [self._dump_image(image_bytes, out_dir)] if image_bytes else []
        )
        return PassportCapture(image_paths=images, mrz_text=mrz_text, viz_fields=viz)

    @staticmethod
    def _extract_viz(response, TextFieldType) -> dict:
        """Поля визуальной зоны (нет в MRZ) -> ключи полей GUI."""
        def f(field_type) -> str:
            try:
                return response.text.get_field_value(field_type) or ""
            except Exception:  # noqa: BLE001
                return ""
        viz = {
            "PATRONYMIC": f(TextFieldType.MIDDLE_NAME),
            "BIRTHPLACE": f(TextFieldType.PLACE_OF_BIRTH),
            "DATE_ISSUE": f(TextFieldType.DATE_OF_ISSUE),
            "ISSUED_BY": f(TextFieldType.AUTHORITY),
        }
        return {k: v for k, v in viz.items() if v}

    def _grab_image_bytes(self, out_dir: str) -> Optional[bytes]:
        """Получить байты изображения для распознавания.

        TODO(железо): здесь вызывается захват кадра со сканера Regula 7017
        (УФ/белый/ИК, 300 dpi). Пока — берём последний скан из out_dir, если есть.
        """
        if os.path.isdir(out_dir):
            files = sorted(
                f for f in os.listdir(out_dir)
                if f.lower().endswith((".jpg", ".jpeg", ".png"))
            )
            if files:
                with open(os.path.join(out_dir, files[-1]), "rb") as fh:
                    return fh.read()
        return None

    @staticmethod
    def _save_response_images(response, out_dir: str) -> List[str]:
        """Сохранить изображения из ответа Regula (если сервис их вернул)."""
        return []  # TODO(железо): извлечь графические поля из response при наличии

    @staticmethod
    def _dump_image(image_bytes: bytes, out_dir: str) -> str:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "passport_00.jpg")
        with open(path, "wb") as fh:
            fh.write(image_bytes)
        return path

    def scan_pages(self, out_dir: str, max_pages: Optional[int] = None) -> List[str]:
        self._load_sdk()
        # TODO(SDK): режим booksheet — последовательное сканирование всех страниц
        #   паспорта при 300 dpi с сохранением в out_dir (см. ТЗ, п.3.1(3), п.5.1).
        raise ScannerError("scan_pages: требуется реализация режима booksheet Regula")


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
    return RegulaScanner(rest_url=cfg.regula_rest_url or None)


def get_document_scanner(cfg: Config) -> BaseScanner:
    """Сканер документов: Mock или Kodak в зависимости от конфигурации."""
    if cfg.mock_scanners:
        return MockScanner(_mock_dir())
    return KodakScanner()


def _mock_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "mock_scans")
