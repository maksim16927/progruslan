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
    """Сканер паспортов Regula 7017 через COM-объект ``READERDEMO.RegulaReader``.

    Поставка Regula Passport Reader SDK (PasspR40.dll и пр.) регистрирует
    COM-компонент ``READERDEMO.RegulaReader``. Подключаемся к нему из Python через
    pywin32 (``win32com.client``). SDK сам управляет устройством и выполняет
    распознавание (встроенный модуль): MRZ, визуальная зона (VIZ), портрет.

    Поддерживаются два режима:
      * захват с устройства — оператор кладёт паспорт, SDK обрабатывает;
      * распознавание из файла — ``DoProcessImage`` (полезно для проверки без
        сканера, по готовому скану).

    Имена/коды взяты из поставки SDK (READERDEMO_TLB, PasspR.h): ProgID,
    GetTextFieldByType / GetMRZLines / GetReaderGraphicsBitmapByFieldType,
    коды полей eVisualFieldType и eGraphicFieldType.
    """
    name = "Regula 7017"
    PROGID = "READERDEMO.RegulaReader"

    # Коды текстовых полей (eVisualFieldType из READERDEMO_TLB).
    _FT = {
        "MRZ_STRINGS": 0x33,
        "SURNAME": 0x08,
        "GIVEN_NAMES": 0x09,
        "MIDDLE_NAME": 0x92,
        "DOC_NUMBER": 0x02,
        "DATE_OF_BIRTH": 0x05,
        "DATE_OF_EXPIRY": 0x03,
        "DATE_OF_ISSUE": 0x04,
        "NATIONALITY": 0x0B,
        "NATIONALITY_CODE": 0x1A,
        "PLACE_OF_BIRTH": 0x06,
        "AUTHORITY": 0x18,
    }
    _GF_PORTRAIT = 0xC9
    _RESULT_TYPE_TEXT = 0x24

    def __init__(self, dll_path: Optional[str] = None,
                 license_path: Optional[str] = None):
        self.dll_path = dll_path
        self.license_path = license_path
        self._reader = None

    # ------------------------------------------------------------------ SDK
    def _load_sdk(self):
        if self._reader is not None:
            return self._reader
        try:
            import win32com.client  # type: ignore  (pywin32, только Windows)
        except ImportError as e:
            raise ScannerError(
                "Не установлен pywin32 (win32com) — нужен для подключения к "
                "COM-объекту Regula. Установите: pip install pywin32."
            ) from e
        try:
            reader = win32com.client.Dispatch(self.PROGID)
        except Exception as e:  # noqa: BLE001
            raise ScannerError(
                f"Не удалось создать COM-объект {self.PROGID}: {e}. "
                "Проверьте, что Regula SDK установлен и зарегистрирован "
                "(regsvr32), а сканер подключён."
            ) from e
        # Включаем распознавание MRZ и графики (портрет).
        for prop, val in (("DoMRZOCR", True), ("DoGraphics", True),
                          ("InBackground", False)):
            try:
                setattr(reader, prop, val)
            except Exception:  # noqa: BLE001 — свойство может называться иначе
                pass
        self._reader = reader
        return self._reader

    def is_available(self) -> bool:
        try:
            self._load_sdk()
            return True
        except ScannerError:
            return False

    # -------------------------------------------------------------- захват
    def capture_passport(self, out_dir: str, timeout_s: int = 30) -> PassportCapture:
        """Захват с устройства: ждём, пока оператор положит паспорт и SDK обработает."""
        reader = self._load_sdk()
        try:
            if hasattr(reader, "Connect"):
                reader.Connect()
        except Exception as e:  # noqa: BLE001
            raise ScannerError(f"Regula: не удалось подключиться к сканеру: {e}") from e
        self._wait_for_result(reader, timeout_s)
        return self._collect(reader, out_dir)

    def process_image(self, image_path: str, out_dir: str) -> PassportCapture:
        """Распознать готовый файл-скан через SDK (без устройства)."""
        reader = self._load_sdk()
        if not os.path.exists(image_path):
            raise ScannerError(f"Файл не найден: {image_path}")
        try:
            reader.DoProcessImage(os.path.abspath(image_path))
        except Exception as e:  # noqa: BLE001
            raise ScannerError(f"Regula: ошибка DoProcessImage: {e}") from e
        return self._collect(reader, out_dir)

    def _wait_for_result(self, reader, timeout_s: int):
        """Ждать появления текстового результата (документ обработан)."""
        import time
        try:
            import pythoncom  # type: ignore
        except ImportError:
            pythoncom = None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if pythoncom is not None:
                pythoncom.PumpWaitingMessages()  # обработать COM-события
            try:
                if reader.IsReaderResultTypeAvailable(self._RESULT_TYPE_TEXT) > 0:
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)
        raise ScannerError(
            "Regula: не дождались результата распознавания (положите паспорт на "
            "сканер). Если устройство недоступно — проверьте подключение."
        )

    # ------------------------------------------------------------- разбор
    def _collect(self, reader, out_dir: str) -> PassportCapture:
        """Собрать MRZ, VIZ и изображения из текущего результата SDK."""
        mrz_text = self._mrz(reader)
        viz = {
            "PATRONYMIC": self._text(reader, "MIDDLE_NAME"),
            "BIRTHPLACE": self._text(reader, "PLACE_OF_BIRTH"),
            "DATE_ISSUE": self._text(reader, "DATE_OF_ISSUE"),
            "ISSUED_BY": self._text(reader, "AUTHORITY"),
        }
        viz = {k: v for k, v in viz.items() if v}
        images = self._save_images(reader, out_dir)
        return PassportCapture(image_paths=images, mrz_text=mrz_text, viz_fields=viz)

    def _text(self, reader, key: str) -> str:
        try:
            val = reader.GetTextFieldByType(self._FT[key])
            return str(val).strip() if val is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    def _mrz(self, reader) -> str:
        """MRZ-строки: сначала GetMRZLines(), иначе поле ft_MRZ_Strings."""
        try:
            lines = reader.GetMRZLines()
            if lines:
                if isinstance(lines, (list, tuple)):
                    return "\n".join(str(x) for x in lines if x)
                return str(lines).replace("\r\n", "\n").strip()
        except Exception:  # noqa: BLE001
            pass
        return self._text(reader, "MRZ_STRINGS").replace("\r\n", "\n")

    def _save_images(self, reader, out_dir: str) -> List[str]:
        """Сохранить портрет владельца из результата SDK."""
        os.makedirs(out_dir, exist_ok=True)
        saved: List[str] = []
        try:
            data = reader.GetReaderGraphicsBitmapByFieldType(self._GF_PORTRAIT)
            if data is not None and not isinstance(data, bool):
                raw = bytes(data)
                if raw:
                    path = os.path.join(out_dir, "portrait.jpg")
                    with open(path, "wb") as fh:
                        fh.write(raw)
                    saved.append(path)
        except Exception:  # noqa: BLE001 — портрет может отсутствовать
            pass
        return saved

    def scan_pages(self, out_dir: str, max_pages: Optional[int] = None) -> List[str]:
        # Постраничный захват разворотов на Regula 7017 — отдельный сценарий SDK,
        # уточняется по документации COM-интерфейса при наличии устройства.
        raise ScannerError(
            "scan_pages для Regula: используйте «Выбрать страницы паспорта…» или "
            "уточните сценарий booksheet по COM-документации SDK."
        )


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
