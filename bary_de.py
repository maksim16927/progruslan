"""
АРМ Оператора по приёму иностранных граждан — десктоп-клиент (Sokrat Helper).

Развитие исходного прототипа bary_de.py под требования технического задания
«АРМ Оператора по приёму иностранных граждан». Вся предметная логика вынесена в
пакет armcore (транслитерация, MRZ, услуги, хранилище, развороты PDF, отчёты,
сканеры, блокировки, документы), а здесь — графический интерфейс PyQt6 и
оркестровка бизнес-процесса из ТЗ:

  1. Сканирование/чтение паспорта (Regula) -> MRZ -> транслитерация -> поля.
  2. Согласие на обработку ПД (формируется первым) + папка клиента на диске.
  3. Комплект документов по чек-листу услуг (договор -> договор+акт).
  4. Перевод паспорта (сканы страниц + шаблон перевода).
  5. Развороты по 4 страницы на лист (2x2, A4 альбом, 300 dpi).
  6. Сшитые документы с Kodak.
  7. Два Excel-реестра (обучение / прочие).

Многопользовательский режим — через сервер (блокировка папок), отчёты и архив —
в общей сетевой папке (см. armcore/config.py).
"""
import os
import sys

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout,
    QCheckBox, QGridLayout, QMessageBox, QScrollArea, QSizePolicy, QFrame, QPlainTextEdit,
    QFileDialog
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QTimer
from PyQt6.QtGui import QColor, QPalette

from armcore import config as arm_config
from armcore import (
    transliteration, mrz, services as arm_services, storage,
    pdf_layout, reports, scanners, documents, winio, mrz_ocr,
)
from armcore.locking import FolderGuard
from armcore.serverclient import ServerClient, ServerUnavailable

# ====== ЛОКАЛИЗАЦИЯ ПОЛЕЙ ======
PLACEHOLDER_LABELS = {
    "FIO": "Фамилия Имя Отчество",
    "PASSPORT_NUMBER": "Номер паспорта",
    "DATE_ISSUE": "Дата выдачи паспорта",
    "REG_ADDRESS": "Адрес регистрации",
    "TODAY": "Дата заполнения",
    "BIRTHDAY": "Дата рождения",
    "COUNTRY_CODE": "Гражданство / код страны",
    "FAMILY": "Фамилия",
    "NAME": "Имя",
    "PATRONYMIC": "Отчество",
    "SEX": "Пол",
    "BIRTHPLACE": "Место рождения",
    "DATE_END": "Срок действия",
    "ISSUED_BY": "Кем выдан",
    "PERSONAL_ID": "Персональный номер",
}

DATA_KEYS = [
    "FIO", "FAMILY", "NAME", "PATRONYMIC", "BIRTHDAY",
    "PASSPORT_NUMBER", "DATE_ISSUE", "DATE_END",
    "REG_ADDRESS", "BIRTHPLACE", "SEX", "ISSUED_BY", "COUNTRY_CODE", "PERSONAL_ID",
]

# Поля, к которым применяется транслитерация lat->cyr.
TRANSLIT_KEYS = ["FAMILY", "NAME", "PATRONYMIC", "BIRTHPLACE", "ISSUED_BY"]

# ====== СТИЛИ (как в исходном прототипе) ======
S_INPUT = """
    QLineEdit {
        font-size: 15px; border-radius: 14px; padding:7px 10px;
        background: #181B1B; color: #FFF; border: 2.5px solid #145C36;
    }
    QLineEdit:focus {border: 2.5px solid #23C883;}
"""
S_LABEL = """
    color: #EEE; font-size: 15px; font-weight: 600;
    border: 2.5px solid #145C36; border-radius: 14px;
    padding: 7px 18px 7px 15px; background: transparent;
"""
S_BTN = """
    QPushButton {
        background: transparent; color: #23C883;
        font-size: 15px; font-weight: 700; border: 2px solid #23C883;
        border-radius: 13px; padding: 9px 18px;
    }
    QPushButton:hover {background: #23C88320;}
    QPushButton:pressed {background: #23C88333;}
"""
S_BTN_ALT = """
    QPushButton {
        background: transparent; color: #138C60;
        font-size: 15px; font-weight: 700; border: 2px solid #138C60;
        border-radius: 13px; padding: 9px 18px;
    }
    QPushButton:hover {background: #138C6022; border-color: #23C883;}
    QPushButton:pressed {background: #138C6040;}
"""
S_CHECK = """
    QCheckBox {color: #FFF; font-size: 14px; font-weight: 500; padding: 5px 12px 5px 2px;}
    QCheckBox::indicator {
        width: 20px; height: 20px; border-radius: 7px;
        border: 2.5px solid #145C36; background: #232725;
    }
    QCheckBox::indicator:checked {background-color: #145C36; border: 2.5px solid #23C883;}
"""


class CardFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background: #161A19; border-radius: 18px; border: 2.5px solid #145C36;
            }
        """)
        self.setWindowOpacity(0.0)
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(450)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def fade_in(self, delay=0):
        def do_anim():
            self.anim.stop()
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.start()
        QTimer.singleShot(delay, do_anim) if delay > 0 else do_anim()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = arm_config.load_config()
        self.passport_scanner = scanners.get_passport_scanner(self.cfg)
        self.doc_scanner = scanners.get_document_scanner(self.cfg)
        self.server = ServerClient(self.cfg.server_url, self.cfg.operator, self.cfg.workstation)

        # Текущая блокировка и временные сканы паспорта (до создания папки клиента).
        self.guard: FolderGuard | None = None
        self.guard_folder: str | None = None
        self.pending_passport_scans: list[str] = []
        self.last_pdf_path: str | None = None
        # Кэш состояния сканера. ВАЖНО: НЕ проверяем при старте — обращение к
        # Regula SDK может блокировать запуск окна. Состояние обновится после
        # первого «Считать паспорт».
        self._scanner_ok: bool | None = None

        self.setWindowTitle("🏛️ АРМ Оператора — Sokrat Helper 🏛️")
        self.resize(1080, 1000)
        self.setMinimumWidth(720)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#111214"))
        self.setPalette(palette)
        self.setStyleSheet("QWidget {background: #111214;}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        outer.addWidget(scroll, stretch=1)

        main = QWidget()
        scroll.setWidget(main)
        ml = QVBoxLayout(main)
        ml.setContentsMargins(26, 16, 26, 16)
        ml.setSpacing(22)

        self.cards = []
        ml.addWidget(self._build_input_card())
        ml.addWidget(self._build_fields_card())
        ml.addWidget(self._build_services_card())
        ml.addWidget(self._build_actions_card())
        ml.addStretch(1)

        outer.addWidget(self._build_statusbar())

        # Heartbeat блокировки (продлеваем, пока работаем с клиентом).
        self.heartbeat = QTimer(self)
        self.heartbeat.timeout.connect(self._refresh_lock)
        self.heartbeat.start(120_000)  # каждые 2 минуты

        delay = 0
        for card in self.cards:
            card.fade_in(delay)
            delay += 110

    # ---------------------------------------------------------------- UI build
    def _build_header(self):
        header = QFrame()
        header.setStyleSheet("""
            QFrame {background: transparent;}
            QLabel#title {color: #FFF; font-size: 30px; font-weight: 800;}
            QLabel#subtitle {color: #B9BEB9; font-size: 13px;}
        """)
        hl = QVBoxLayout(header)
        hl.setContentsMargins(36, 18, 36, 2)
        hl.setSpacing(2)
        title = QLabel("🏛️ АРМ Оператора по приёму иностранных граждан")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle = QLabel("Regula 7017 • Kodak SceyeX • ГОСТ 7.79-2000 (Б)")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hl.addWidget(title)
        hl.addWidget(subtitle)
        return header

    def _build_input_card(self):
        card = CardFrame()
        v = QVBoxLayout(card)
        v.setContentsMargins(22, 16, 22, 18)
        v.setSpacing(10)

        # Кнопки чтения паспорта.
        row = QHBoxLayout()
        row.setSpacing(10)
        read_btn = QPushButton("📷 Считать паспорт (Regula)")
        read_btn.setStyleSheet(S_BTN)
        read_btn.clicked.connect(self.on_read_passport)
        consent_btn = QPushButton("📷📄 Считать паспорт + Согласие")
        consent_btn.setStyleSheet(S_BTN)
        consent_btn.clicked.connect(self.on_read_and_consent)
        mrz_btn = QPushButton("🔎 Распознать MRZ из текста")
        mrz_btn.setStyleSheet(S_BTN_ALT)
        mrz_btn.clicked.connect(self.on_parse_mrz)
        translit_btn = QPushButton("🔤 Транслитерация lat→cyr")
        translit_btn.setStyleSheet(S_BTN_ALT)
        translit_btn.clicked.connect(self.on_transliterate)
        row.addWidget(read_btn)
        row.addWidget(consent_btn)
        row.addWidget(mrz_btn)
        row.addWidget(translit_btn)
        v.addLayout(row)

        # Автозахват: сканер сам считывает паспорт, когда его подносят.
        self.auto_check = QCheckBox("Автозахват: считывать паспорт при поднесении")
        self.auto_check.setStyleSheet(S_CHECK)
        self.auto_check.toggled.connect(self._toggle_auto_capture)
        v.addWidget(self.auto_check)

        # Ручной выбор готовых сканов с диска (без сканера) — диалог выбора файлов.
        pick_row = QHBoxLayout()
        pick_row.setSpacing(10)
        for text, slot in [
            ("🖼 Выбрать скан паспорта…", self.on_pick_passport),
            ("📚 Выбрать страницы паспорта…", self.on_pick_pages),
            ("📑 Выбрать файлы документа…", self.on_pick_document),
        ]:
            b = QPushButton(text)
            b.setStyleSheet(S_BTN_ALT)
            b.clicked.connect(slot)
            pick_row.addWidget(b)
        v.addLayout(pick_row)

        # Поле для ручной вставки MRZ (2 строки).
        lbl = QLabel("MRZ паспорта (2 строки) либо данные через ';':")
        lbl.setStyleSheet("color:#FFF; font-size:14px; font-weight:600;")
        v.addWidget(lbl)
        self.mrz_entry = QPlainTextEdit()
        self.mrz_entry.setFixedHeight(64)
        self.mrz_entry.setStyleSheet("""
            QPlainTextEdit {font-family: Consolas, "Courier New", "DejaVu Sans Mono", monospace;
                font-size: 14px; border-radius: 8px;
                padding: 6px 10px; background: #151818; color: #fff; border: 2px solid #145C36;}
            QPlainTextEdit:focus {border: 2px solid #23C883;}
        """)
        v.addWidget(self.mrz_entry)

        self.cards.append(card)
        return card

    def _build_fields_card(self):
        card = CardFrame()
        v = QVBoxLayout(card)
        v.setContentsMargins(22, 15, 22, 20)
        grid = QGridLayout()
        grid.setVerticalSpacing(5)
        grid.setHorizontalSpacing(24)
        # Поля в две колонки: первая половина — слева, вторая — справа,
        # чтобы все данные паспорта помещались на одном экране.
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.passport_fields = {}
        per_col = (len(DATA_KEYS) + 1) // 2
        for i, key in enumerate(DATA_KEYS):
            label = QLabel(PLACEHOLDER_LABELS.get(key, key) + ":")
            label.setStyleSheet(S_LABEL)
            field = QLineEdit()
            field.setMinimumWidth(120)
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            field.setStyleSheet(S_INPUT)
            self.passport_fields[key] = field
            row = i % per_col
            col = (i // per_col) * 2
            grid.addWidget(label, row, col)
            grid.addWidget(field, row, col + 1)
        v.addLayout(grid)
        self.cards.append(card)
        return card

    def _build_services_card(self):
        card = CardFrame()
        v = QVBoxLayout(card)
        v.setContentsMargins(22, 15, 22, 20)
        v.setSpacing(8)
        title = QLabel("Перечень услуг (чек-лист)")
        title.setStyleSheet("color:#FFF; font-size:17px; font-weight:600;")
        v.addWidget(title)

        grid = QGridLayout()
        self.service_checks = {}
        per_col = (len(arm_services.SERVICES) + 2) // 3
        for i, svc in enumerate(arm_services.SERVICES):
            check = QCheckBox(svc.label)
            check.setStyleSheet(S_CHECK)
            self.service_checks[svc.label] = check
            grid.addWidget(check, i % per_col, i // per_col)
        v.addLayout(grid)

        # Комментарий для услуги «Прочее».
        crow = QHBoxLayout()
        clbl = QLabel("Комментарий (Прочее):")
        clbl.setStyleSheet("color:#EEE; font-size:14px; font-weight:600;")
        self.comment_entry = QLineEdit()
        self.comment_entry.setStyleSheet(S_INPUT)
        crow.addWidget(clbl)
        crow.addWidget(self.comment_entry, 1)
        v.addLayout(crow)

        self.cards.append(card)
        return card

    def _build_actions_card(self):
        card = CardFrame()
        v = QVBoxLayout(card)
        v.setContentsMargins(22, 14, 22, 16)
        v.setSpacing(10)

        row1 = QHBoxLayout()
        row1.setSpacing(12)
        for text, slot in [
            ("📄 Сформировать комплект", self.on_generate),
            ("📚 Сканировать страницы (Regula)", self.on_scan_pages),
            ("🗂️ Сформировать развороты 2×2", self.on_make_spreads),
        ]:
            b = QPushButton(text)
            b.setStyleSheet(S_BTN)
            b.clicked.connect(slot)
            row1.addWidget(b)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(12)
        for text, slot, style in [
            ("📑 Сканировать документ (Kodak)", self.on_scan_document, S_BTN_ALT),
            ("📄 Открыть PDF", self.on_open_pdf, S_BTN_ALT),
            ("🖨️ Печать последнего PDF", self.on_print_pdf, S_BTN_ALT),
            ("📂 Открыть папку клиента", self.on_open_folder, S_BTN_ALT),
        ]:
            b = QPushButton(text)
            b.setStyleSheet(style)
            b.clicked.connect(slot)
            row2.addWidget(b)
        v.addLayout(row2)

        self.cards.append(card)
        return card

    def _build_statusbar(self):
        bar = QFrame()
        bar.setStyleSheet("QFrame{background:#0D0F0E;} QLabel{color:#8FA89A; font-size:12px;}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 6, 20, 6)
        self.status_label = QLabel()
        h.addWidget(self.status_label)
        h.addStretch(1)
        self._update_status()
        return bar

    # ---------------------------------------------------------------- helpers
    def _fields(self) -> dict:
        return {k: e.text().strip() for k, e in self.passport_fields.items()}

    def _selected_services(self) -> list:
        return [label for label, c in self.service_checks.items() if c.isChecked()]

    def _set_fields(self, values: dict):
        for key, val in values.items():
            if key in self.passport_fields and val:
                self.passport_fields[key].setText(val)
        self._recompute_fio()

    def _recompute_fio(self):
        parts = [self.passport_fields[k].text().strip() for k in ("FAMILY", "NAME", "PATRONYMIC")]
        fio = " ".join(p for p in parts if p)
        if fio:
            self.passport_fields["FIO"].setText(fio)

    def _refresh_scanner_state(self):
        """Пересчитать состояние сканера (в режиме оборудования). Безопасно."""
        if self.cfg.mock_scanners:
            self._scanner_ok = None
            return
        try:
            self._scanner_ok = bool(self.passport_scanner.is_available())
        except Exception:  # noqa: BLE001 — статус не должен ронять программу
            self._scanner_ok = False

    def _update_status(self, extra: str = ""):
        server_state = "?"
        try:
            self.server.health()
            server_state = "онлайн"
        except ServerUnavailable:
            server_state = "офлайн (локальные блокировки)"
        if self.cfg.mock_scanners:
            scan_mode = "Сканеры: MOCK"
        elif self._scanner_ok is None:
            scan_mode = "Сканер: оборудование"
        elif self._scanner_ok:
            scan_mode = "Сканер: подключён ✓"
        else:
            scan_mode = "Сканер: не найден ✗"
        lock = f" • блокировка: {self.guard_folder}" if self.guard_folder else ""
        self.status_label.setText(
            f"Оператор: {self.cfg.operator} • АРМ: {self.cfg.workstation} • "
            f"Сервер: {server_state} • {scan_mode} • Archive: {self.cfg.archive_root}"
            f"{lock}{(' • ' + extra) if extra else ''}"
        )

    def _ensure_client(self):
        """Создать/найти папку клиента и взять блокировку. Возвращает путь или None."""
        fio = self.passport_fields["FIO"].text().strip()
        if not fio:
            QMessageBox.warning(self, "Нет ФИО", "Сначала заполните ФИО клиента.")
            return None
        try:
            folder = storage.ensure_client_folder(self.cfg.archive_root, fio)
        except OSError as e:
            QMessageBox.critical(self, "Хранилище недоступно",
                                 f"Не удалось создать папку в {self.cfg.archive_root}:\n{e}")
            return None

        folder_id = os.path.basename(folder)
        if self.guard_folder != folder_id:
            if self.guard:
                self.guard.release()
            self.guard = FolderGuard(self.cfg, folder_id, folder)
            status = self.guard.acquire()
            self.guard_folder = folder_id
            if status.read_only:
                QMessageBox.warning(
                    self, "Папка занята",
                    f"Клиента «{folder_id}» сейчас редактирует оператор "
                    f"{status.owner}. Доступ только для чтения.",
                )
                self.guard = None
                self.guard_folder = None
                self._update_status()
                return None
            self._update_status()
        return folder

    def _refresh_lock(self):
        if self.guard:
            self.guard.refresh()

    def _import_pending_scans(self, folder: str):
        """Перенести временно сохранённые сканы паспорта в папку клиента."""
        if not self.pending_passport_scans:
            return
        import shutil
        dest = storage.subfolder(folder, "passport_raw")
        for path in self.pending_passport_scans:
            if os.path.exists(path):
                try:
                    shutil.copy2(path, os.path.join(dest, os.path.basename(path)))
                except OSError:
                    pass
        self.pending_passport_scans = []

    # ---------------------------------------------------------------- actions
    def on_read_passport(self):
        """Считать паспорт на Regula: захват -> MRZ -> транслитерация -> поля.

        Без реального оборудования (mock-режим) сканер не подставляет файлы
        автоматически — оператор сам выбирает нужные сканы в диалоге.
        """
        if self.cfg.mock_scanners:
            self.on_pick_passport()
            return
        import tempfile
        try:
            tmp = tempfile.mkdtemp(prefix="arm_passport_")
            cap = self.passport_scanner.capture_passport(tmp)
        except scanners.ScannerError as e:
            self._scanner_ok = False
            self._update_status()
            QMessageBox.critical(self, "Сканер недоступен", str(e))
            return
        self._scanner_ok = True
        self.pending_passport_scans = list(cap.image_paths)
        if cap.mrz_text:
            self.mrz_entry.setPlainText(cap.mrz_text)
        result = mrz.parse(cap.mrz_text) if cap.mrz_text else None
        if result:
            self._set_fields(result.to_fields())
            if cap.viz_fields:
                self._set_fields(cap.viz_fields)
            msg = "Паспорт считан, поля заполнены."
            if not result.valid:
                msg += "\n\nВнимание:\n" + "\n".join(result.warnings)
            QMessageBox.information(self, "Готово", msg)
        else:
            QMessageBox.information(
                self, "Сканы получены",
                f"Получено сканов: {len(cap.image_paths)}. MRZ не распознан — "
                "заполните поля вручную или вставьте MRZ в поле и нажмите «Распознать MRZ».")
        self._update_status()

    # --- автозахват паспорта (сканер сам считывает при поднесении) --------
    def _toggle_auto_capture(self, on: bool):
        if self.cfg.mock_scanners:
            if on:
                QMessageBox.information(self, "Автозахват",
                                        "В mock-режиме автозахват недоступен.")
                self.auto_check.setChecked(False)
            return
        if on:
            try:
                self.passport_scanner.watch_begin()
            except scanners.ScannerError as e:
                QMessageBox.critical(self, "Сканер недоступен", str(e))
                self.auto_check.setChecked(False)
                return
            if not hasattr(self, "auto_timer"):
                self.auto_timer = QTimer(self)
                self.auto_timer.setInterval(400)
                self.auto_timer.timeout.connect(self._auto_capture_tick)
            self.auto_timer.start()
            self._update_status("автозахват включён — поднесите паспорт")
        else:
            if hasattr(self, "auto_timer"):
                self.auto_timer.stop()
            self._update_status()

    def _auto_capture_tick(self):
        import tempfile
        try:
            tmp = tempfile.mkdtemp(prefix="arm_auto_")
            cap = self.passport_scanner.watch_poll(tmp)
        except Exception:  # noqa: BLE001 — тик не должен ронять программу
            return
        if not cap or not (cap.mrz_text or cap.image_paths):
            return
        # Паспорт считан: заполнить поля БЕЗ модальных окон, чтобы не мешать.
        self.pending_passport_scans = list(cap.image_paths)
        if cap.mrz_text:
            self.mrz_entry.setPlainText(cap.mrz_text)
        result = mrz.parse(cap.mrz_text) if cap.mrz_text else None
        if result:
            self._set_fields(result.to_fields())
            if cap.viz_fields:
                self._set_fields(cap.viz_fields)
            note = "паспорт считан автоматически"
            if not result.valid:
                note += " (проверьте: " + "; ".join(result.warnings[:1]) + ")"
            self._update_status(note)
        else:
            self._update_status("скан получен, MRZ не распознан — проверьте поля")

    def on_read_and_consent(self):
        """Считать паспорт и сразу сформировать Согласие + открыть папку клиента.

        Быстрый сценарий оператора (без выбора услуг): паспорт -> поля ->
        согласие.docx (+PDF, если есть конвертер) -> папка клиента на экране.
        """
        self.on_read_passport()
        if not self.passport_fields["FIO"].text().strip():
            return  # паспорт не считался — сообщение уже показано
        folder = self._ensure_client()
        if not folder:
            return
        self._import_pending_scans(folder)
        try:
            created = documents.generate_documents(
                self.cfg.templates_dir, self._fields(), ["Согласие"], folder)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка формирования", str(e))
            return
        open_path = None
        for docx_path in created:
            open_path = docx_path
            pdf_path = os.path.splitext(docx_path)[0] + ".pdf"
            try:
                winio.docx_to_pdf(docx_path, pdf_path)
                self.last_pdf_path = pdf_path
                open_path = pdf_path
            except Exception:  # noqa: BLE001 — без конвертера остаётся .docx
                pass
        if not created:
            QMessageBox.warning(
                self, "Согласие",
                "Шаблон согласия не найден. Положите файл "
                f"«{documents.DOC_TEMPLATES['Согласие']['file']}» в папку шаблонов:\n"
                f"{self.cfg.templates_dir}")
            return
        winio_open_folder(open_path)  # открыть сам документ согласия (PDF или .docx)

    # --- выбор готовых сканов с диска (без оборудования) ------------------
    _IMG_FILTER = "Изображения (*.jpg *.jpeg *.png *.tif *.tiff *.bmp);;Все файлы (*)"

    def _pick_files(self, title: str) -> list[str]:
        files, _ = QFileDialog.getOpenFileNames(
            self, title, self._scans_dir(), self._IMG_FILTER)
        return files

    def _scans_dir(self) -> str:
        """Папка, в которой открывается диалог выбора сканов (mock_scans)."""
        here = os.path.dirname(os.path.abspath(__file__))
        d = os.path.join(here, "mock_scans", "passport")
        return d if os.path.isdir(d) else ""

    def on_pick_passport(self):
        """Выбрать с диска готовый скан паспорта (главную страницу)."""
        files = self._pick_files("Выберите скан паспорта")
        if not files:
            return
        self.pending_passport_scans = list(files)
        # Пытаемся распознать MRZ прямо из выбранного скана (OCR).
        recognized = False
        for path in files:
            text = mrz_ocr.mrz_text_from_image(path)
            if not text:
                continue
            result = mrz.parse(text)
            if result:
                self.mrz_entry.setPlainText(text)
                self._set_fields(result.to_fields())
                recognized = True
                msg = f"Скан выбран и MRZ распознан из изображения.\nФайлов: {len(files)}."
                if not result.valid:
                    msg += "\n\nВнимание:\n" + "\n".join(result.warnings)
                QMessageBox.information(self, "Распознано", msg)
                break
        if not recognized:
            diag = mrz_ocr.ocr_diagnostics()
            problems = []
            if not diag["tesseract"]:
                problems.append("• не установлен Tesseract OCR "
                                "(скачайте установщик UB Mannheim, либо укажите путь "
                                "в переменной ARM_TESSERACT)")
            if not diag["passporteye"]:
                problems.append("• не установлен пакет passporteye "
                                "(pip install passporteye)")
            if not diag["pytesseract"]:
                problems.append("• не установлен пакет pytesseract "
                                "(pip install pytesseract)")
            base = (f"Выбрано файлов: {len(files)}.\nMRZ из изображения распознать "
                    "не удалось.\n\n")
            if problems:
                base += ("Похоже, не настроено распознавание:\n"
                         + "\n".join(problems) + "\n\n")
            else:
                base += "Возможно, плохое качество скана или нет зоны MRZ.\n\n"
            base += "Впишите данные вручную или вставьте текст MRZ и нажмите «Распознать MRZ»."
            QMessageBox.information(self, "Скан выбран", base)
        self._update_status()

    def on_pick_pages(self):
        """Выбрать с диска страницы паспорта (для разворотов и перевода)."""
        files = self._pick_files("Выберите страницы паспорта")
        if not files:
            return
        folder = self._ensure_client()
        if not folder:
            return
        dest = storage.subfolder(folder, "passport_raw")
        copied = 0
        for path in files:
            try:
                shutil.copy2(path, os.path.join(dest, os.path.basename(path)))
                copied += 1
            except OSError:
                pass
        QMessageBox.information(
            self, "Страницы добавлены",
            f"Скопировано страниц: {copied}\nв папку:\n{dest}\n\n"
            "Теперь нажмите «Сформировать развороты 2×2».")

    def on_pick_document(self):
        """Выбрать с диска файлы сшитого документа и собрать их в PDF."""
        files = self._pick_files("Выберите файлы документа")
        if not files:
            return
        folder = self._ensure_client()
        if not folder:
            return
        out = os.path.join(storage.subfolder(folder, "other"), "документ.pdf")
        try:
            pdf_layout.images_to_pdf(files, out)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        self.last_pdf_path = out
        QMessageBox.information(self, "Готово",
                               f"Документ собран ({len(files)} стр.):\n{out}")

    def on_parse_mrz(self):
        text = self.mrz_entry.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Пусто", "Вставьте текст MRZ (2 строки).")
            return
        # Поддержка старого формата «данные через ;».
        if ";" in text and "\n" not in text:
            self._paste_semicolon(text)
            return
        result = mrz.parse(text)
        if not result:
            QMessageBox.warning(self, "Не распознано",
                                "Не удалось выделить две строки MRZ (формат TD3).")
            return
        self._set_fields(result.to_fields())
        if result.valid:
            QMessageBox.information(self, "Готово", "MRZ распознан, поля заполнены.")
        else:
            QMessageBox.warning(self, "Распознано с замечаниями",
                                "Поля заполнены, но:\n" + "\n".join(result.warnings))

    def _paste_semicolon(self, text: str):
        values = [v.strip() for v in text.split(";")]
        for i, key in enumerate(DATA_KEYS):
            self.passport_fields[key].setText(values[i] if i < len(values) else "")

    def on_transliterate(self):
        """Транслитерировать латинские поля в кириллицу (ГОСТ 7.79-2000, Б)."""
        for key in TRANSLIT_KEYS:
            val = self.passport_fields[key].text().strip()
            if val:
                self.passport_fields[key].setText(transliteration.transliterate_name(val))
        self._recompute_fio()
        QMessageBox.information(self, "Транслитерация",
                               "Поля переведены на кириллицу. Проверьте и при необходимости поправьте вручную.")

    def on_generate(self):
        folder = self._ensure_client()
        if not folder:
            return
        fields = self._fields()
        selected = self._selected_services()
        comment = self.comment_entry.text().strip()

        self._import_pending_scans(folder)

        # Согласие формируется первым (ТЗ, п.3.1.2 / 5.4), затем комплект по услугам.
        doc_labels = ["Согласие"] + arm_services.documents_for_selection(selected)
        try:
            created = documents.generate_documents(
                self.cfg.templates_dir, fields, doc_labels, folder)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка формирования", str(e))
            return

        # Конвертация документов в PDF (рядом с .docx). Без LibreOffice/Word
        # на рабочем месте — просто пропускаем, .docx остаются.
        pdfs = []
        for docx_path in created:
            pdf_path = os.path.splitext(docx_path)[0] + ".pdf"
            try:
                winio.docx_to_pdf(docx_path, pdf_path)
                pdfs.append(pdf_path)
            except Exception:  # noqa: BLE001 — конвертация не должна ронять процесс
                pass
        if pdfs:
            self.last_pdf_path = pdfs[-1]

        # Excel-реестры (обучение / прочие).
        try:
            report_path = reports.record_client(self.cfg, fields, selected, comment)
        except Exception as e:  # noqa: BLE001
            report_path = None
            QMessageBox.warning(self, "Реестр", f"Не удалось записать в Excel: {e}")

        # Общая БД на сервере (если доступен).
        try:
            self.server.upsert_client(fields, selected, comment)
        except ServerUnavailable:
            pass

        missing = [d for d in doc_labels
                   if d in documents.DOC_TEMPLATES and
                   not os.path.exists(os.path.join(self.cfg.templates_dir,
                                                    documents.DOC_TEMPLATES[d]["file"]))]
        msg = f"Папка клиента:\n{folder}\n\nСоздано документов: {len(created)}"
        if created:
            msg += "\n - " + "\n - ".join(os.path.basename(p) for p in created)
        if pdfs:
            msg += f"\n\nСоздано PDF: {len(pdfs)}"
        if missing:
            msg += ("\n\nНе найдены шаблоны (положите .docx в сетевую папку шаблонов):\n - "
                    + "\n - ".join(documents.DOC_TEMPLATES[d]["file"] for d in missing))
        if report_path:
            reg = "обучение" if arm_services.is_study_client(selected) else "прочие"
            msg += f"\n\nЗапись добавлена в реестр «{reg}»:\n{report_path}"
        QMessageBox.information(self, "Готово", msg)
        winio_open_folder(folder)

    def on_scan_pages(self):
        """Постранично отсканировать паспорт на Regula (режим booksheet).

        Regula снимает по одному развороту: цикл «положите разворот -> захват ->
        сканировать следующий?». Каждый снимок сохраняется как page_NN.jpg.
        Для MockScanner остаётся прежний путь (scan_pages из папки-источника).
        """
        folder = self._ensure_client()
        if not folder:
            return
        dest = storage.subfolder(folder, "passport_raw")

        if not isinstance(self.passport_scanner, scanners.RegulaScanner):
            try:
                pages = self.passport_scanner.scan_pages(dest)
            except scanners.ScannerError as e:
                QMessageBox.critical(self, "Сканер недоступен", str(e))
                return
            QMessageBox.information(self, "Готово",
                                   f"Отсканировано страниц: {len(pages)}\nСохранены в:\n{dest}")
            return

        import shutil
        import tempfile
        # Нумерация продолжается после уже отсканированных страниц.
        existing = [f for f in os.listdir(dest)
                    if f.lower().startswith("page_")] if os.path.isdir(dest) else []
        num = len(existing) + 1
        saved = 0
        # Подтверждение только перед ПЕРВОЙ страницей; дальше вопрос
        # «сканировать следующий?» сам служит стартом захвата.
        QMessageBox.information(
            self, "Сканирование страниц",
            f"Положите разворот {num} на стекло сканера и нажмите ОК —\n"
            "начнётся захват.")
        while True:
            tmp = tempfile.mkdtemp(prefix="arm_regula_page_")
            try:
                cap = self.passport_scanner.capture_passport(tmp)
            except scanners.ScannerError as e:
                QMessageBox.critical(self, "Ошибка сканирования", str(e))
                break
            if not cap.image_paths:
                QMessageBox.warning(self, "Нет изображения",
                                    "Сканер не вернул снимок страницы. Попробуйте ещё раз.")
            else:
                dst = os.path.join(dest, f"page_{num:02d}.jpg")
                shutil.copyfile(cap.image_paths[0], dst)
                saved += 1
                num += 1
            more = QMessageBox.question(
                self, "Сканирование страниц",
                f"Сохранено страниц: {saved}.\n"
                f"Положите разворот {num} и нажмите «Да» — сканирование начнётся сразу.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if more != QMessageBox.StandardButton.Yes:
                break
        if saved:
            QMessageBox.information(self, "Готово",
                                   f"Отсканировано страниц: {saved}\nСохранены в:\n{dest}")

    def on_make_spreads(self):
        """Сформировать развороты по 4 страницы на лист (2x2, A4 альбом, 300 dpi)."""
        folder = self._ensure_client()
        if not folder:
            return
        raw_dir = storage.subfolder(folder, "passport_raw")
        images = pdf_layout.list_images(raw_dir)
        if not images:
            QMessageBox.warning(self, "Нет сканов",
                                f"В папке нет изображений:\n{raw_dir}\n"
                                "Сначала отсканируйте страницы паспорта.")
            return
        out = os.path.join(storage.subfolder(folder, "passport_spreads"), "паспорт.pdf")
        caption = f"{self.passport_fields['FIO'].text().strip()} — Паспорт"
        try:
            pdf_layout.make_spreads_pdf(images, out, per_sheet=4, grid=(2, 2),
                                        landscape=False, caption=caption)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        self.last_pdf_path = out
        QMessageBox.information(self, "Готово",
                               f"Развороты сформированы ({len(images)} стр. → "
                               f"{(len(images) + 3) // 4} лист.):\n{out}")

    def on_scan_document(self):
        """Отсканировать сшитый документ на Kodak (многостраничный, 300 dpi)."""
        folder = self._ensure_client()
        if not folder:
            return
        import tempfile
        try:
            tmp = tempfile.mkdtemp(prefix="arm_kodak_")
            pages = self.doc_scanner.scan_document(tmp)
        except scanners.ScannerError as e:
            QMessageBox.critical(self, "Сканер недоступен", str(e))
            return
        out = os.path.join(storage.subfolder(folder, "other"), "документ_kodak.pdf")
        try:
            pdf_layout.images_to_pdf(pages, out)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        self.last_pdf_path = out
        QMessageBox.information(self, "Готово",
                               f"Документ отсканирован ({len(pages)} стр.):\n{out}")

    def on_open_pdf(self):
        """Открыть (просмотреть) PDF: последний созданный или выбрать из папки клиента."""
        if self.last_pdf_path and os.path.exists(self.last_pdf_path):
            winio_open_folder(self.last_pdf_path)
            return
        # Последнего PDF нет — предложить выбрать файл из папки клиента.
        fio = self.passport_fields["FIO"].text().strip()
        start = storage.find_client_folder(self.cfg.archive_root, fio) if fio else None
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите PDF", start or "", "PDF (*.pdf);;Все файлы (*)")
        if path:
            self.last_pdf_path = path
            winio_open_folder(path)

    def on_print_pdf(self):
        if not self.last_pdf_path or not os.path.exists(self.last_pdf_path):
            QMessageBox.warning(self, "Нет PDF",
                                "Сначала сформируйте развороты или отсканируйте документ.")
            return
        try:
            winio.print_file(self.last_pdf_path)
            QMessageBox.information(self, "Печать", f"Отправлено на печать:\n{self.last_pdf_path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка печати", str(e))

    def on_open_folder(self):
        """Открыть папку клиента; если её ещё нет — создать (по ФИО)."""
        folder = self._ensure_client()
        if folder:
            winio_open_folder(folder)

    def closeEvent(self, event):
        if self.guard:
            self.guard.release()
        event.accept()


def winio_open_folder(path: str):
    import platform
    import subprocess
    if not (path and os.path.exists(path)):
        return
    system = platform.system()
    if system == "Windows":
        os.startfile(os.path.normpath(path))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget { background: #111214; }
        QMessageBox { background-color: #232525; }
        QMessageBox QLabel { color: #FFF; font-size: 14px; }
        QMessageBox QPushButton {
            color: #FFF; background: #0A5833; border-radius: 9px;
            padding: 5px 17px; font-weight: 600;
        }
        QMessageBox QPushButton:hover { background: #23C883; }
    """)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
