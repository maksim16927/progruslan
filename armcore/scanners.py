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

    # Расширенный набор полей для диагностики (что реально отдаёт SDK).
    _FT_DUMP = {
        "Document_Number": 0x02, "Date_of_Expiry": 0x03, "Date_of_Issue": 0x04,
        "Date_of_Birth": 0x05, "Place_of_Birth": 0x06, "Sex": 0x07,
        "Surname": 0x08, "Given_Names": 0x09, "Nationality": 0x0B,
        "Authority": 0x18, "Surname_And_Given_Names": 0x19,
        "Nationality_Code": 0x1A, "Address": 0x1B,
        "MRZ_String1": 0x20, "MRZ_String2": 0x21, "MRZ_Strings": 0x33,
        "Place_of_Issue": 0x27, "Personal_Number": 0x0D,
        "Middle_Name": 0x92, "Surname_RUS": 0x7E, "Given_Names_RUS": 0x7F,
        "Place_of_Registration": 0x45, "Authority_Code": 0x49,
    }

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
        # Включаем ПОЛНУЮ обработку документа (как в C#-примере поставки SDK):
        #   DoVisualOCR  — чтение визуальной зоны (отчество, место рождения,
        #                  дата выдачи, орган) — без него fieldList пустой;
        #   DoOCRAnalize — лексический анализ распознанного текста;
        #   DoGraphics   — портрет/графика; DoLocateDocument/DoDocumentType —
        #   локализация и определение типа; DoReceiveImages — получать снимки.
        for prop, val in (
            ("OptionsEnabled", True),   # без этого записи свойств могут игнорироваться
            ("DoMRZOCR", True),
            ("DoVisualOCR", True),
            ("DoOCRAnalize", True),
            ("DoGraphics", True),
            ("DoLocateDocument", True),
            ("DoDocumentType", True),
            ("DoReceiveImages", True),
            ("DoReceiveAllScannedImages", True),
            ("InBackground", False),
        ):
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

    # Типы результатов (eRPRM_ResultType) — для диагностики готовности.
    _RESULT_TYPES = {
        "RawImage": 0x01,
        "Graphics": 0x06,
        "Text": 0x24,
        "OCRLexicalAnalyze": 0x25,
        "DocumentTypesCandidates": 0x08,
    }

    # -------------------------------------------------------------- захват
    def capture_passport(self, out_dir: str, timeout_s: int = 60) -> PassportCapture:
        """Захват с устройства: ждём, пока оператор положит паспорт и SDK обработает."""
        reader = self._load_sdk()
        try:
            if hasattr(reader, "Connect"):
                reader.Connect()
        except Exception as e:  # noqa: BLE001
            raise ScannerError(f"Regula: не удалось подключиться к сканеру: {e}") from e
        self._wait_for_result(reader, timeout_s)
        return self._collect(reader, out_dir)

    def diagnostics(self, reader=None) -> dict:
        """Какие типы результатов сейчас доступны у ридера (для отладки)."""
        reader = reader or self._load_sdk()
        info = {}
        for name, code in self._RESULT_TYPES.items():
            try:
                info[name] = int(reader.IsReaderResultTypeAvailable(code))
            except Exception as e:  # noqa: BLE001
                info[name] = f"err: {e}"
        # Пробное чтение ключевых полей.
        info["mrz_preview"] = self._mrz(reader)[:60]
        info["surname_preview"] = self._text(reader, "SURNAME")
        return info

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

    def _result_ready(self, reader) -> bool:
        """Готов ли результат: есть РЕАЛЬНЫЕ данные (MRZ или распознанные поля).

        Не полагаемся только на «тип результата доступен» — он бывает =1, но с
        пустым fieldList (распознавание ещё не завершено). Ждём настоящие данные.
        """
        if self._mrz(reader):
            return True
        if self._lexical_fields(reader):
            return True
        return False

    def _wait_for_result(self, reader, timeout_s: int):
        """Ждать появления результата (документ положен и обработан)."""
        import time
        try:
            import pythoncom  # type: ignore
        except ImportError:
            pythoncom = None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if pythoncom is not None:
                pythoncom.PumpWaitingMessages()  # прокачать COM-события
            if self._result_ready(reader):
                return
            time.sleep(0.3)
        # Диагностика — что реально доступно у ридера на момент таймаута.
        diag = self.diagnostics(reader)
        raise ScannerError(
            "Regula: не дождались результата распознавания за "
            f"{timeout_s} c. Диагностика результата: {diag}. "
            "Подсветка моргает — значит захват есть; пришлите эту строку разработчику."
        )

    # ------------------------------------------------------------- разбор
    def _select_text_result(self, reader):
        """Выбрать ДОСТУПНЫЙ текстовый результат (предпочтительно OCRLexicalAnalyze)."""
        # Выбираем тот тип, который реально доступен (0x25 несёт распознанные поля).
        for code in (0x25, self._RESULT_TYPE_TEXT):  # OCRLexicalAnalyze, Text
            try:
                if int(reader.IsReaderResultTypeAvailable(code)) <= 0:
                    continue
            except Exception:  # noqa: BLE001
                continue
            for args in ((code, 0, 0, ""), (code, 0, 0)):
                try:
                    reader.CheckReaderResult(*args)
                    return
                except Exception:  # noqa: BLE001
                    continue

    def flags_info(self, reader=None) -> dict:
        """Прочитать обратно флаги обработки — применились ли наши настройки."""
        reader = reader or self._load_sdk()
        out = {}
        for prop in ("OptionsEnabled", "DoMRZOCR", "DoVisualOCR", "DoOCRAnalize",
                     "DoGraphics", "DoLocateDocument", "DoDocumentType",
                     "DoReceiveImages", "AutoScan", "InBackground"):
            try:
                out[prop] = getattr(reader, prop)
            except Exception as e:  # noqa: BLE001
                out[prop] = f"err: {e}"
        return out

    def lexical_xml(self, reader=None) -> str:
        """XML результата OCRLexicalAnalyze (там лежат распознанные поля)."""
        reader = reader or self._load_sdk()
        for code in (0x25, self._RESULT_TYPE_TEXT):  # OCRLexical, Text
            for args in ((code, 0, 0), (code, 0)):
                try:
                    xml = reader.CheckReaderResultXML(*args)
                    if xml and "Field" in str(xml):
                        return str(xml)
                except Exception:  # noqa: BLE001
                    continue
        # вернуть хоть что-то для диагностики
        for args in ((0x25, 0, 0), (0x25, 0)):
            try:
                xml = reader.CheckReaderResultXML(*args)
                if xml:
                    return str(xml)
            except Exception:  # noqa: BLE001
                continue
        return ""

    def _lexical_fields(self, reader) -> dict:
        """Разобрать XML распознавания -> {код_поля(int): значение(str)}.

        Толерантно к формату: ищем элементы с FieldType и берём значение из
        Field_Visual / Field_MRZ / Buf_Text / Value.
        """
        xml = self.lexical_xml(reader)
        out: dict = {}
        if not xml:
            return out
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(xml)
        except Exception:  # noqa: BLE001
            return out

        def local(tag: str) -> str:
            return tag.split("}")[-1]

        for el in root.iter():
            children = {local(c.tag): (c.text or "").strip() for c in list(el)}
            if "FieldType" not in children:
                continue
            ft = children.get("FieldType", "")
            val = (children.get("Field_Visual") or children.get("Buf_Text")
                   or children.get("Field_MRZ") or children.get("Value") or "")
            if not ft or not val:
                continue
            try:
                out[int(ft)] = val
            except ValueError:
                pass
        return out

    # Коды полей (eVisualFieldType) -> ключи полей GUI.
    _FT_TO_GUI = {
        0x08: "FAMILY", 0x09: "NAME", 0x92: "PATRONYMIC",
        0x02: "PASSPORT_NUMBER", 0x05: "BIRTHDAY", 0x03: "DATE_END",
        0x04: "DATE_ISSUE", 0x06: "BIRTHPLACE", 0x18: "ISSUED_BY",
        0x1A: "COUNTRY_CODE", 0x0B: "COUNTRY_CODE",
        0x1B: "REG_ADDRESS", 0x45: "REG_ADDRESS",
        0x0D: "PERSONAL_ID",
    }

    def _collect(self, reader, out_dir: str) -> PassportCapture:
        """Собрать MRZ, поля и изображения из текущего результата SDK."""
        self._select_text_result(reader)
        mrz_text = self._mrz(reader)
        # Поля из XML распознавания (надёжнее GetTextFieldByType на этой версии).
        fields = self._lexical_fields(reader)
        viz: dict = {}
        for code, value in fields.items():
            gui_key = self._FT_TO_GUI.get(code)
            if gui_key and value:
                viz.setdefault(gui_key, value)
        # Прямое чтение полей как дополнение (если что-то отдаётся отдельно).
        for key, code in (("PATRONYMIC", "MIDDLE_NAME"), ("BIRTHPLACE", "PLACE_OF_BIRTH"),
                          ("DATE_ISSUE", "DATE_OF_ISSUE"), ("ISSUED_BY", "AUTHORITY")):
            if not viz.get(key):
                val = self._text(reader, code)
                if val:
                    viz[key] = val
        images = self._save_images(reader, out_dir)
        return PassportCapture(image_paths=images, mrz_text=mrz_text, viz_fields=viz)

    def _text(self, reader, key: str) -> str:
        try:
            val = reader.GetTextFieldByType(self._FT[key])
            return str(val).strip() if val is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    def dump_text_fields(self, reader=None) -> dict:
        """Все непустые текстовые поля, которые отдаёт SDK (для диагностики)."""
        reader = reader or self._load_sdk()
        self._select_text_result(reader)
        out = {}
        for name, code in self._FT_DUMP.items():
            try:
                val = reader.GetTextFieldByType(code)
                val = str(val).strip() if val is not None else ""
            except Exception:  # noqa: BLE001
                val = ""
            if val:
                out[name] = val
        return out

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

    @staticmethod
    def _to_bytes(data) -> bytes:
        """Привести данные графики из COM (SAFEARRAY/tuple/memoryview/str) к bytes."""
        if data is None or isinstance(data, bool):
            return b""
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if isinstance(data, memoryview):
            return data.tobytes()
        if isinstance(data, (list, tuple)):
            try:
                return bytes(bytearray(int(x) & 0xFF for x in data))
            except Exception:  # noqa: BLE001
                return b""
        if isinstance(data, str):
            # Иногда возвращается base64-строка.
            import base64
            try:
                return base64.b64decode(data)
            except Exception:  # noqa: BLE001
                return data.encode("latin-1", "ignore")
        return b""

    def _select_graphics_result(self, reader):
        """Выбрать результат графики перед чтением битмапа (как в C#-примере)."""
        for args in (
            (0x06, 0, 0, ""),   # CheckReaderResult(Graphics, 0, 0, "")
            (0x06, 0, 0),
        ):
            try:
                reader.CheckReaderResult(*args)
                return
            except Exception:  # noqa: BLE001 — пробуем следующую сигнатуру
                continue

    def _portrait_bytes(self, reader) -> bytes:
        """Получить байты портрета: с предварительным выбором результата графики."""
        self._select_graphics_result(reader)
        for getter, args in (
            ("GetReaderGraphicsBitmapByFieldType", (self._GF_PORTRAIT,)),
            ("GetReaderGraphicsBitmapByFieldTypeAndSource", (self._GF_PORTRAIT, 0)),
            ("GetGraphicFieldByTypeAndSource", (self._GF_PORTRAIT, 0)),
        ):
            try:
                fn = getattr(reader, getter, None)
                if fn is None:
                    continue
                raw = self._to_bytes(fn(*args))
                if raw:
                    return raw
            except Exception:  # noqa: BLE001
                continue
        return b""

    def graphics_info(self, reader=None) -> str:
        """Диагностика портрета: тип и размер данных, что отдаёт SDK."""
        reader = reader or self._load_sdk()
        self._select_graphics_result(reader)
        out = []
        try:
            avail = int(reader.IsReaderResultTypeAvailable(0x06))
            out.append(f"Graphics доступно={avail}")
        except Exception as e:  # noqa: BLE001
            out.append(f"IsReaderResultTypeAvailable(Graphics) err: {e}")
        try:
            data = reader.GetReaderGraphicsBitmapByFieldType(self._GF_PORTRAIT)
            out.append(f"тип={type(data).__name__}, байт={len(self._to_bytes(data))}")
        except Exception as e:  # noqa: BLE001
            out.append(f"GetReaderGraphicsBitmapByFieldType err: {e}")
        return "; ".join(out)

    def _raw_image_bytes(self, reader) -> bytes:
        """Сырой снимок документа (белый свет) — если портрет недоступен."""
        # Пробуем выбрать результат сырого изображения и взять кадр.
        for args in ((0x01, 0, 0, ""), (0x01, 0, 0)):  # RawImage
            try:
                reader.CheckReaderResult(*args)
                break
            except Exception:  # noqa: BLE001
                continue
        for getter, args in (
            ("GetReaderEOSBitmapImageByLightIndex", (0,)),
            ("GetReaderImageByLightIndex", (0,)),
            ("GetReaderImage", (0, 0)),
        ):
            try:
                fn = getattr(reader, getter, None)
                if fn is None:
                    continue
                raw = self._to_bytes(fn(*args))
                if raw:
                    return raw
            except Exception:  # noqa: BLE001
                continue
        return b""

    def _save_images(self, reader, out_dir: str) -> List[str]:
        """Сохранить портрет владельца и/или сырой снимок паспорта из SDK."""
        os.makedirs(out_dir, exist_ok=True)
        saved: List[str] = []
        portrait = self._portrait_bytes(reader)
        if portrait:
            path = os.path.join(out_dir, "portrait.jpg")
            with open(path, "wb") as fh:
                fh.write(portrait)
            saved.append(path)
        raw = self._raw_image_bytes(reader)
        if raw:
            path = os.path.join(out_dir, "passport_scan.jpg")
            with open(path, "wb") as fh:
                fh.write(raw)
            saved.append(path)
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
