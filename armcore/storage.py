"""
Файловое хранилище клиента.

ТЗ, п.3.3: структура на сервере —
    X:\\Archive\\Фамилия_Имя_Отчество_ДДММГГГГ\\
где ДДММГГГГ — дата ПЕРВИЧНОГО обращения.

Состав папки клиента (ТЗ):
    сырой скан паспорта (все страницы)
    развороты паспорта по 4 страницы на листе
    файл перевода (docx, редактируемый)
    договор
    акт
    медкомиссия
    финальный скан перевода паспорта (нотариальный)
    прочие документы (регистрация, мигр. карта и т.д.)
"""
from __future__ import annotations

import os
import re
from datetime import date
from typing import Dict, List, Optional


# Подпапки внутри папки клиента — упорядоченное хранение состава из ТЗ.
SUBFOLDERS: Dict[str, str] = {
    "passport_raw": "01_Паспорт_сырые_сканы",
    "passport_spreads": "02_Паспорт_развороты",
    "translation": "03_Перевод_паспорта",
    "contract": "04_Договор",
    "act": "05_Акт",
    "consent": "06_Согласие",
    "medical": "07_Медкомиссия",
    "translation_final": "08_Перевод_нотариальный",
    "other": "09_Прочие_документы",
}

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(part: str) -> str:
    """Убрать из части имени символы, недопустимые в именах папок Windows."""
    cleaned = _FORBIDDEN.sub("", (part or "").strip())
    cleaned = cleaned.strip(" .")          # Windows не любит хвостовые пробелы/точки
    return re.sub(r"\s+", " ", cleaned)


def today_ddmmyyyy() -> str:
    return date.today().strftime("%d%m%Y")


def split_fio(fio: str) -> List[str]:
    return [p for p in sanitize(fio).split(" ") if p]


def client_folder_name(family: str, name: str = "", patronymic: str = "",
                       date_str: Optional[str] = None) -> str:
    """Сформировать имя папки: Фамилия_Имя_Отчество_ДДММГГГГ."""
    date_str = date_str or today_ddmmyyyy()
    parts = [sanitize(p) for p in (family, name, patronymic) if sanitize(p)]
    base = "_".join(parts) if parts else "Клиент"
    return f"{base}_{date_str}"


def client_folder_name_from_fio(fio: str, date_str: Optional[str] = None) -> str:
    parts = split_fio(fio)
    while len(parts) < 3:
        parts.append("")
    return client_folder_name(parts[0], parts[1], parts[2], date_str=date_str)


def _name_prefix_from_fio(fio: str) -> str:
    """Фамилия_Имя_Отчество без даты — для поиска существующей папки клиента."""
    parts = split_fio(fio)
    return "_".join(parts) if parts else "Клиент"


def find_client_folder(archive_root: str, fio: str) -> Optional[str]:
    """Найти уже существующую папку клиента (по ФИО, без учёта даты)."""
    if not os.path.isdir(archive_root):
        return None
    prefix = _name_prefix_from_fio(fio) + "_"
    matches = [
        d for d in os.listdir(archive_root)
        if os.path.isdir(os.path.join(archive_root, d)) and d.startswith(prefix)
    ]
    if not matches:
        return None
    # Если их несколько — берём самую раннюю по дате обращения (она в имени).
    matches.sort()
    return os.path.join(archive_root, matches[0])


def ensure_client_folder(archive_root: str, fio: str,
                         date_str: Optional[str] = None) -> str:
    """Вернуть путь к папке клиента, создав её при необходимости.

    Если папка уже есть (за любую дату обращения) — переиспользуем её, чтобы
    дата ПЕРВИЧНОГО обращения не перезаписывалась (ТЗ, п.3.3).
    """
    existing = find_client_folder(archive_root, fio)
    if existing:
        path = existing
    else:
        folder_name = client_folder_name_from_fio(fio, date_str=date_str)
        path = os.path.join(archive_root, folder_name)
    os.makedirs(path, exist_ok=True)
    for sub in SUBFOLDERS.values():
        os.makedirs(os.path.join(path, sub), exist_ok=True)
    return path


def subfolder(client_folder: str, key: str) -> str:
    """Путь к подпапке внутри папки клиента (создаётся при обращении)."""
    if key not in SUBFOLDERS:
        raise KeyError(f"Неизвестная подпапка: {key}")
    path = os.path.join(client_folder, SUBFOLDERS[key])
    os.makedirs(path, exist_ok=True)
    return path
