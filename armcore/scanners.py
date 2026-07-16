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
    image_source: str = ""   # откуда снимок: full_page / portrait / none (диагностика)


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
        self._proc_count = 0       # сколько раз пришло OnProcessingFinished
        self._events_ok = False    # удалось ли привязать COM-события
        self._capture_cmd = "не вызывалась"  # какой командой запускали захват

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

        parent = self

        class _ReaderEvents:
            def OnProcessingFinished(self, *args):  # noqa: N802 — имя события SDK
                parent._proc_count += 1

        try:
            reader = win32com.client.DispatchWithEvents(self.PROGID, _ReaderEvents)
            self._events_ok = True
        except Exception:  # noqa: BLE001 — события не привязались, опрос как фолбэк
            self._events_ok = False
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
            ("RotateResultImages", True),        # выровнять кадр вертикально
            ("DoChangeOrientationByFace", True), # ориентация по лицу
            ("AutoScan", True),                  # автозахват при появлении документа
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

    def _clear_results(self, reader):
        """Очистить предыдущий результат, чтобы не подтянулись старые фото/текст."""
        try:
            reader.ClearResults()
        except Exception:  # noqa: BLE001
            pass

    # -------------------------------------------------------------- захват
    def capture_passport(self, out_dir: str, timeout_s: int = 60) -> PassportCapture:
        """Захват с устройства: ждём, пока оператор положит паспорт и SDK обработает."""
        reader = self._load_sdk()
        try:
            if hasattr(reader, "Connect"):
                reader.Connect()
        except Exception as e:  # noqa: BLE001
            raise ScannerError(f"Regula: не удалось подключиться к сканеру: {e}") from e
        self._clear_results(reader)  # сбросить прошлый результат — фото будет новым
        self._start_capture(reader)
        start_count = self._proc_count
        self._wait_for_result(reader, timeout_s, start_count=start_count)
        return self._collect(reader, out_dir)

    def _start_capture(self, reader):
        """Явно запустить захват документа.

        Одного Connect() мало: SDK подключается и ждёт команду. Пробуем
        известные имена команды сканирования из разных версий COM-интерфейса;
        AutoScan (включён в _load_sdk) остаётся подстраховкой. Запоминаем,
        какая команда сработала / какие пробовали — для диагностики.
        """
        tried = []
        for cmd in ("Process", "DoScan", "Scan", "StartScan", "Capture",
                    "DoProcess", "ScanDocument", "GetImages"):
            fn = getattr(reader, cmd, None)
            if fn is None:
                continue
            try:
                fn()
                self._capture_cmd = cmd
                return
            except Exception as e:  # noqa: BLE001 — метод есть, но не сработал
                tried.append(f"{cmd}: {e}")
        self._capture_cmd = "нет (" + ("; ".join(tried)[:200] or "методы не найдены") + ")"

    @staticmethod
    def _com_methods(reader) -> list:
        """Реальные имена методов COM-объекта (для подбора команды сканирования)."""
        names = set()
        for attr in ("_prop_map_get_", "_prop_map_put_"):
            names.update(getattr(reader, attr, {}) or {})
        for n in dir(reader):
            if not n.startswith("_"):
                names.add(n)
        oleobj = getattr(reader, "_oleobj_", None)
        if oleobj is not None:
            try:
                ti = oleobj.GetTypeInfo()
                attr = ti.GetTypeAttr()
                for i in range(attr.cFuncs):
                    fd = ti.GetFuncDesc(i)
                    names.add(ti.GetNames(fd.memid)[0])
            except Exception:  # noqa: BLE001 — typeinfo недоступен
                pass
        return sorted(names)

    # ------------------------------------------------- автозахват (watch)
    def watch_begin(self):
        """Включить режим ожидания: сканер сам захватит поднесённый паспорт.

        Дальше периодически вызывать watch_poll() (например, по QTimer) —
        когда документ обработан, она вернёт PassportCapture.
        """
        reader = self._load_sdk()
        try:
            if hasattr(reader, "Connect"):
                reader.Connect()
        except Exception as e:  # noqa: BLE001
            raise ScannerError(f"Regula: не удалось подключиться к сканеру: {e}") from e
        self._clear_results(reader)
        self._watch_count = self._proc_count
        self._watch_last = None
        self._watch_seen = False

    def watch_poll(self, out_dir: str, quiet_s: float = 1.5):
        """Неблокирующая проверка автозахвата.

        Возвращает PassportCapture, когда документ положили и SDK его обработал
        (после «тихого периода» quiet_s — все проходы света завершены),
        иначе None. После выдачи результата снова ждёт следующий документ.
        """
        import time
        if self._reader is None:
            return None
        try:
            import pythoncom  # type: ignore
            pythoncom.PumpWaitingMessages()
        except ImportError:
            pass
        reader = self._reader
        # Признак активности: пришло событие обработки, либо (без событий)
        # появился готовый снимок.
        signaled = self._proc_count > getattr(self, "_watch_count", 0)
        if not signaled and not self._events_ok:
            # Без событий: сигналим один раз, когда снимок ПОЯВИЛСЯ.
            has = self._has_portrait(reader)
            signaled = has and not getattr(self, "_watch_seen", False)
            self._watch_seen = has
        if signaled:
            self._watch_count = self._proc_count
            self._watch_last = time.monotonic()
            return None
        last = getattr(self, "_watch_last", None)
        if last is not None and time.monotonic() - last >= quiet_s:
            self._watch_last = None
            cap = self._collect(reader, out_dir)
            self._clear_results(reader)  # готов к следующему паспорту
            self._watch_seen = False
            return cap
        return None

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
        info["capture_cmd"] = getattr(self, "_capture_cmd", "не вызывалась")
        info["com_methods"] = ", ".join(self._com_methods(reader))[:600]
        return info

    def process_image(self, image_path: str, out_dir: str) -> PassportCapture:
        """Распознать готовый файл-скан через SDK (без устройства)."""
        reader = self._load_sdk()
        if not os.path.exists(image_path):
            raise ScannerError(f"Файл не найден: {image_path}")
        self._clear_results(reader)  # сбросить прошлый результат
        try:
            reader.DoProcessImage(os.path.abspath(image_path))
        except Exception as e:  # noqa: BLE001
            raise ScannerError(f"Regula: ошибка DoProcessImage: {e}") from e
        return self._collect(reader, out_dir)

    def _has_text(self, reader) -> bool:
        """Есть ли распознанный текст (MRZ или поля)."""
        if self._mrz(reader):
            return True
        if self._lexical_fields(reader):
            return True
        return False

    def _has_portrait(self, reader) -> bool:
        """Доступен ли белый снимок/портрет (графика)."""
        try:
            return int(reader.IsReaderResultTypeAvailable(0x06)) > 0
        except Exception:  # noqa: BLE001
            return False

    def _wait_for_result(self, reader, timeout_s: int, grace_s: float = 6.0,
                         start_count: int = 0):
        """Ждать завершения обработки документа.

        Если COM-события привязаны — ждём ``OnProcessingFinished`` и небольшой
        «тихий период» (документ сканируется в несколько проходов: ИК→MRZ,
        белый→снимок/портрет; событие приходит на каждый проход, берём после
        последнего). Иначе — опрос по наличию текста и портрета.
        """
        import time
        try:
            import pythoncom  # type: ignore
        except ImportError:
            pythoncom = None

        def pump():
            if pythoncom is not None:
                pythoncom.PumpWaitingMessages()

        deadline = time.monotonic() + timeout_s

        if self._events_ok:
            last_event_count = start_count
            last_event_at = None
            while time.monotonic() < deadline:
                pump()
                if self._proc_count > last_event_count:
                    last_event_count = self._proc_count
                    last_event_at = time.monotonic()
                # После последнего прохода ждём тишины ~1.5 c и забираем результат.
                if last_event_at is not None and time.monotonic() - last_event_at >= 1.5:
                    return
                time.sleep(0.1)
            if last_event_at is not None or self._has_text(reader) or self._has_portrait(reader):
                return
            diag = self.diagnostics(reader)
            raise ScannerError(
                f"Regula: документ не обработан за {timeout_s} c. "
                "Положите паспорт на стекло разворотом вниз ДО нажатия кнопки и "
                "проверьте, что сканер виден в фирменной программе Regula. "
                f"Диагностика: {diag}."
            )

        # --- фолбэк: опрос (события не привязались) ---
        first_seen_at = None
        while time.monotonic() < deadline:
            pump()
            text = self._has_text(reader)
            portrait = self._has_portrait(reader)
            if text and portrait:
                return
            if text or portrait:
                if first_seen_at is None:
                    first_seen_at = time.monotonic()
                elif time.monotonic() - first_seen_at >= grace_s:
                    return
            time.sleep(0.3)
        if self._has_text(reader) or self._has_portrait(reader):
            return
        diag = self.diagnostics(reader)
        raise ScannerError(
            "Regula: не дождались результата распознавания за "
            f"{timeout_s} c. Положите паспорт на стекло разворотом вниз ДО "
            "нажатия кнопки и проверьте, что сканер виден в фирменной программе "
            f"Regula. Диагностика: {diag}. Пришлите эту строку разработчику."
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
        """Собрать MRZ, поля и изображения из текущего результата SDK.

        Основные поля (ФИО, номер, даты, гражданство, личный номер) берёт из MRZ
        надёжный разбор в GUI — их из SDK НЕ маппим, чтобы не затирать обрезанными
        значениями. Из SDK берём только то, чего нет в MRZ.
        """
        self._select_text_result(reader)
        mrz_text = self._mrz(reader)
        viz: dict = {}
        for key, code in (("PATRONYMIC", "MIDDLE_NAME"), ("BIRTHPLACE", "PLACE_OF_BIRTH"),
                          ("DATE_ISSUE", "DATE_OF_ISSUE"), ("ISSUED_BY", "AUTHORITY")):
            val = self._text(reader, code)
            if val:
                viz[key] = val
        images, source = self._save_images(reader, out_dir)
        return PassportCapture(image_paths=images, mrz_text=mrz_text,
                               viz_fields=viz, image_source=source)

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

    def dump_all_codes(self, reader=None) -> dict:
        """Перебрать коды полей 0x01..0xB0 и вернуть все непустые значения.

        Диагностика: показывает ВСЁ, что распознал сканер, с кодами полей —
        чтобы найти, под какими кодами лежат дата выдачи, отчество, место
        рождения, адрес, орган.
        """
        reader = reader or self._load_sdk()
        self._select_text_result(reader)
        out = {}
        for code in range(0x01, 0xB1):
            try:
                val = reader.GetTextFieldByType(code)
                val = str(val).strip() if val is not None else ""
            except Exception:  # noqa: BLE001
                val = ""
            if val:
                out[hex(code)] = val
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

    def _portrait_from_xml(self, reader) -> bytes:
        """Портрет из XML результата (base64 JPEG, fieldType 201/Portrait).

        Самый надёжный способ: SDK кладёт портрет готовым JPEG в base64 в
        TImageField с fieldType=201 (gf_Portrait). Берём из любого XML, где он есть.
        """
        import base64
        import xml.etree.ElementTree as ET

        def local(tag: str) -> str:
            return tag.split("}")[-1]

        for code in (0x06, 0x01, 0x25, self._RESULT_TYPE_TEXT):  # Graphics/RawImage/...
            for args in ((code, 0, 0), (code, 0)):
                try:
                    xml = reader.CheckReaderResultXML(*args)
                except Exception:  # noqa: BLE001
                    continue
                if not xml or "Portrait" not in str(xml):
                    continue
                try:
                    root = ET.fromstring(str(xml))
                except Exception:  # noqa: BLE001
                    continue
                for el in root.iter():
                    if local(el.tag) != "TImageField":
                        continue
                    ch = {local(c.tag): c for c in list(el)}
                    ft = (ch.get("fieldType").text or "").strip() if ch.get("fieldType") is not None else ""
                    if ft and ft not in ("201",):
                        continue
                    # Найти текст value внутри valueList.
                    for v in el.iter():
                        if local(v.tag) == "value" and (v.text or "").strip():
                            try:
                                return base64.b64decode(v.text.strip())
                            except Exception:  # noqa: BLE001
                                pass
        return b""

    def _portrait_bytes(self, reader) -> bytes:
        """Получить байты портрета: из XML (base64 JPEG) либо COM-методом."""
        raw = self._portrait_from_xml(reader)
        if raw:
            return raw
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

    def images_info(self, reader=None) -> dict:
        """Размеры снимков по методам/свету — диагностика полной страницы."""
        reader = reader or self._load_sdk()
        for args in ((0x01, 0, 0, ""), (0x01, 0, 0)):
            try:
                reader.CheckReaderResult(*args)
                break
            except Exception:  # noqa: BLE001
                continue
        out = {}
        for light in (0x06, 0x02, 0x04):
            for getter in ("GetReaderBitmapImageByLightIndex",
                           "GetReaderEOSBitmapImageByLightIndex"):
                fn = getattr(reader, getter, None)
                if fn is None:
                    continue
                try:
                    out[f"{getter}({light})"] = len(self._to_bytes(fn(light)))
                except Exception as e:  # noqa: BLE001
                    out[f"{getter}({light})"] = f"err"
        for idx in range(0, 3):
            fn = getattr(reader, "GetReaderBitmapImage", None)
            if fn is not None:
                try:
                    out[f"GetReaderBitmapImage({idx})"] = len(self._to_bytes(fn(idx)))
                except Exception:  # noqa: BLE001
                    out[f"GetReaderBitmapImage({idx})"] = "err"
        return out

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
        """Полный снимок страницы паспорта (белый свет).

        Собираем кандидатов разными методами/источниками света и берём САМЫЙ
        большой кадр — это и есть полная страница (а не вырезка).
        """
        # Выбрать результат сырого изображения.
        for args in ((0x01, 0, 0, ""), (0x01, 0, 0)):  # RawImage
            try:
                reader.CheckReaderResult(*args)
                break
            except Exception:  # noqa: BLE001
                continue

        WHITE = (0x06, 0x02, 0x04, 0x00000006)  # White_Full / White_Top / White_Side
        eos: list = []        # полный кадр сенсора (без обрезки по документу)
        cropped: list = []    # обрезанный по контуру документа

        # EOS-методы дают ВЕСЬ кадр (с полями) — приоритет, чтобы верх не срезался.
        for light in WHITE:
            fn = getattr(reader, "GetReaderEOSBitmapImageByLightIndex", None)
            if fn is not None:
                try:
                    raw = self._to_bytes(fn(light))
                    if raw:
                        eos.append(raw)
                except Exception:  # noqa: BLE001
                    pass
        for idx in range(0, 4):
            fn = getattr(reader, "GetReaderEOSBitmapImage", None)
            if fn is not None:
                try:
                    raw = self._to_bytes(fn(idx))
                    if raw:
                        eos.append(raw)
                except Exception:  # noqa: BLE001
                    pass
        # Обрезанные по документу — запасной вариант.
        for light in WHITE:
            fn = getattr(reader, "GetReaderBitmapImageByLightIndex", None)
            if fn is not None:
                try:
                    raw = self._to_bytes(fn(light))
                    if raw:
                        cropped.append(raw)
                except Exception:  # noqa: BLE001
                    pass
        for idx in range(0, 4):
            fn = getattr(reader, "GetReaderBitmapImage", None)
            if fn is not None:
                try:
                    raw = self._to_bytes(fn(idx))
                    if raw:
                        cropped.append(raw)
                except Exception:  # noqa: BLE001
                    pass

        if eos:
            return self._enhance(max(eos, key=len))   # полный кадр + улучшение
        if cropped:
            return self._enhance(max(cropped, key=len))
        return b""

    @staticmethod
    def _enhance(raw: bytes) -> bytes:
        """Лёгкое улучшение читаемости снимка: автоконтраст + резкость.

        Помогает проявить бледный текст (например, верхнюю строку названия).
        """
        if not raw:
            return raw
        try:
            from io import BytesIO
            from PIL import ImageOps, ImageEnhance
            img = Image.open(BytesIO(raw)).convert("RGB")
            img = ImageOps.autocontrast(img, cutoff=1)
            img = ImageEnhance.Contrast(img).enhance(1.15)
            img = ImageEnhance.Sharpness(img).enhance(1.6)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=92)
            return buf.getvalue()
        except Exception:  # noqa: BLE001 — не вышло улучшить, отдаём как есть
            return raw

    @staticmethod
    def _rotate180(raw: bytes) -> bytes:
        """Развернуть JPEG-кадр на 180° (полный кадр сенсора приходит перевёрнутым)."""
        if not raw:
            return raw
        try:
            from io import BytesIO
            img = Image.open(BytesIO(raw)).rotate(180, expand=True).convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=92)
            return buf.getvalue()
        except Exception:  # noqa: BLE001 — не вышло развернуть, отдаём как есть
            return raw

    def _save_images(self, reader, out_dir: str):
        """Сохранить ПОЛНЫЙ скан паспорта (без вырезки лица).

        Берём доступный снимок страницы (белый кадр / графическое поле) и
        сохраняем как passport_scan.jpg — лицо отдельно не вырезаем.
        Возвращает (список путей, источник снимка) — источник нужен для
        диагностики случая «вместо страницы только фото».
        """
        os.makedirs(out_dir, exist_ok=True)
        source = "full_page"
        raw = self._raw_image_bytes(reader)
        if not raw:
            source = "portrait"
            raw = self._portrait_bytes(reader)
        if not raw:
            return [], "none"
        path = os.path.join(out_dir, "passport_scan.jpg")
        with open(path, "wb") as fh:
            fh.write(raw)
        return [path], source

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
    """Сканер документов (Kodak SceyeX и др.) — многостраничные сшитые документы.

    Сканирование идёт через **NAPS2** (бесплатная программа, naps2.com) и её
    консоль ``NAPS2.Console.exe``. NAPS2 надёжно работает с TWAIN-драйверами,
    в т.ч. 32-битными (через собственный worker), и сам выдаёт изображения —
    не нужен ни 32-битный Python, ни ручная возня с TWAIN-состояниями.

    Настройка: установить NAPS2; в его окне создать **профиль** для сканера
    (TWAIN, цвет, 300 dpi) и задать имя профиля в ``ARM_NAPS2_PROFILE``
    (по умолчанию ``kodak``). Путь к NAPS2.Console.exe — авто или ``ARM_NAPS2``.
    """
    name = "Сканер документов (NAPS2)"

    def __init__(self, device_name: Optional[str] = None):
        self.device_name = device_name

    @staticmethod
    def _naps2_exe() -> Optional[str]:
        """Найти NAPS2.Console.exe: env ARM_NAPS2 -> стандартные пути."""
        env = os.environ.get("ARM_NAPS2")
        if env and os.path.exists(env):
            return env
        for base in (r"C:\Program Files\NAPS2", r"C:\Program Files (x86)\NAPS2"):
            exe = os.path.join(base, "NAPS2.Console.exe")
            if os.path.exists(exe):
                return exe
        return None

    def is_available(self) -> bool:
        return self._naps2_exe() is not None

    def scan_document(self, out_dir: str) -> List[str]:
        """Отсканировать документ через NAPS2.Console. Вернуть пути страниц (jpg)."""
        import subprocess
        os.makedirs(out_dir, exist_ok=True)
        exe = self._naps2_exe()
        if not exe:
            raise ScannerError(
                "Не найден NAPS2.Console.exe. Установите NAPS2 (naps2.com) или "
                "задайте путь к NAPS2.Console.exe в переменной ARM_NAPS2."
            )

        profile = os.environ.get("ARM_NAPS2_PROFILE", "kodak")
        # Шаблон вывода: NAPS2 заменит $(n) на номер страницы -> doc1.jpg, doc2.jpg
        out_tpl = os.path.join(out_dir, "doc$(n).jpg")
        cmd = [exe, "-o", out_tpl, "-p", profile, "--force"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired as e:
            raise ScannerError("NAPS2: сканирование заняло слишком долго (таймаут).") from e

        import glob
        paths = sorted(glob.glob(os.path.join(out_dir, "doc*.jpg")))
        if not paths:
            output = ((proc.stdout or "") + (proc.stderr or "")).strip()
            raise ScannerError(
                "NAPS2: страниц не получено. Проверьте, что создан профиль "
                f"«{profile}» (NAPS2 -> Профили) для вашего сканера. "
                f"Вывод NAPS2: {output[:300] or '(пусто)'}"
            )
        return paths


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
