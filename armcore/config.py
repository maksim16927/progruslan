"""
Единая конфигурация АРМ.

Все пути, адрес сервера и параметры качества сканов собраны в одном месте.
Значения можно переопределить через переменные окружения или файл
``arm_config.json`` рядом с программой — это удобно при разворачивании на
4 рабочих местах, где у каждого может отличаться имя оператора, но путь к
сетевому хранилищу общий.
"""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, asdict, field


# Корень сетевого хранилища из ТЗ: X:\Archive\Фамилия_Имя_Отчество_ДДММГГГГ\
DEFAULT_ARCHIVE_ROOT = r"X:\Archive"

# Качество сканов из ТЗ: всё оборудование — 300 dpi, цветной PDF.
SCAN_DPI = 300
SCAN_COLOR_MODE = "RGB"          # цветное изображение
PDF_JPEG_QUALITY = 92            # без потери читаемости

# Размеры листа A4 при 300 dpi (книжная ориентация), в пикселях.
A4_PORTRAIT_PX = (2480, 3508)
# A4 альбомная — для разворотов 2x2.
A4_LANDSCAPE_PX = (3508, 2480)


@dataclass
class Config:
    # Сетевое хранилище (общая папка сервера, смонтированная как X:\).
    archive_root: str = DEFAULT_ARCHIVE_ROOT
    # Папка с .docx-шаблонами (общая, на сервере).
    templates_dir: str = os.path.join(DEFAULT_ARCHIVE_ROOT, "_templates")
    # Адрес сервера блокировок/БД (отдельный сервер из ТЗ).
    server_url: str = "http://127.0.0.1:8770"
    # Имя оператора и АРМ — попадают в журнал блокировок и отчёты.
    operator: str = os.environ.get("ARM_OPERATOR", os.environ.get("USERNAME", "operator"))
    workstation: str = field(default_factory=socket.gethostname)
    # Качество сканов.
    scan_dpi: int = SCAN_DPI
    # Использовать заглушки сканеров (нет железа) — для разработки/тестов.
    mock_scanners: bool = True
    # URL локального сервиса распознавания Regula Document Reader (Web API).
    # Пусто = SDK не подключён (используется mock или файловый OCR).
    regula_rest_url: str = ""
    # Excel-отчёты (ведутся на сервере/в корне Archive).
    report_study_name: str = "Реестр_обучение.xlsx"      # обучение (договор+акт)
    report_other_name: str = "Реестр_прочие.xlsx"        # все остальные

    @property
    def report_study_path(self) -> str:
        return os.path.join(self.archive_root, self.report_study_name)

    @property
    def report_other_path(self) -> str:
        return os.path.join(self.archive_root, self.report_other_name)


def _config_file_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "arm_config.json")


def load_config() -> Config:
    """Грузит конфиг: дефолты -> arm_config.json -> переменные окружения."""
    cfg = Config()
    path = _config_file_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, val in data.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, val)
        except (OSError, json.JSONDecodeError):
            pass  # битый конфиг не должен ронять программу — используем дефолты

    # Переопределения из окружения (приоритетнее файла).
    env_map = {
        "ARM_ARCHIVE_ROOT": "archive_root",
        "ARM_TEMPLATES_DIR": "templates_dir",
        "ARM_SERVER_URL": "server_url",
        "ARM_OPERATOR": "operator",
        "ARM_REGULA_URL": "regula_rest_url",
    }
    for env_key, attr in env_map.items():
        if os.environ.get(env_key):
            setattr(cfg, attr, os.environ[env_key])

    if os.environ.get("ARM_MOCK_SCANNERS"):
        cfg.mock_scanners = os.environ["ARM_MOCK_SCANNERS"].lower() in ("1", "true", "yes")

    return cfg


def save_config(cfg: Config) -> str:
    path = _config_file_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, ensure_ascii=False, indent=2)
    return path
