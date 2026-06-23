"""
Формирование документов из .docx-шаблонов.

ТЗ, п.3.5: «Система хранит .docx шаблоны в сетевой папке. Заполнение через
python-docx или аналог».

Здесь заполнение сделано без MS Word и без тяжёлых зависимостей: .docx — это ZIP,
внутри word/document.xml; плейсхолдеры заменяются прямо в XML. Это надёжно для
простых шаблонов и работает на любой ОС. Формат плейсхолдера: <<KEY>>.
"""
from __future__ import annotations

import datetime
import os
import zipfile
from io import BytesIO
from typing import Dict, List

from . import storage


# Документ -> (имя файла шаблона, ключ подпапки в папке клиента).
DOC_TEMPLATES: Dict[str, Dict[str, str]] = {
    "Согласие": {"file": "согласие.docx", "subfolder": "consent"},
    "Договор на обучение": {"file": "договор_обучение.docx", "subfolder": "contract"},
    "Договор сопровождение": {"file": "договор_сопровождение.docx", "subfolder": "contract"},
    "Акт": {"file": "акт.docx", "subfolder": "act"},
    "Полис ДМС": {"file": "полис_дмс.docx", "subfolder": "other"},
    "Перевод паспорта ГУ": {"file": "перевод_гу.docx", "subfolder": "translation"},
    "Перевод паспорта ПВС": {"file": "перевод_пвс.docx", "subfolder": "translation"},
}


def _replace_in_xml(xml_text: str, fields: Dict[str, str]) -> str:
    for key, val in fields.items():
        safe = (val or "")
        # Плейсхолдер в XML может быть экранирован: &lt;&lt;KEY&gt;&gt;
        xml_text = xml_text.replace(f"&lt;&lt;{key}&gt;&gt;", safe)
        xml_text = xml_text.replace(f"<<{key}>>", safe)
    return xml_text


def fill_template(template_path: str, fields: Dict[str, str], output_path: str) -> str:
    """Заполнить .docx-шаблон значениями и сохранить как output_path."""
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Шаблон не найден: {template_path}")

    out_buf = BytesIO()
    with zipfile.ZipFile(template_path, "r") as zin, \
         zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            # Текст бывает в основном документе, колонтитулах и сносках.
            if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                data = _replace_in_xml(data.decode("utf-8"), fields).encode("utf-8")
            zout.writestr(item, data)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(out_buf.getvalue())
    return output_path


def _output_name(doc_label: str, fields: Dict[str, str]) -> str:
    parts = storage.split_fio(fields.get("FIO", ""))
    base = "_".join(parts[:3]) if parts else "Документ"
    date_str = datetime.datetime.now().strftime("%d.%m.%Y")
    return f"{base}_{doc_label}_{date_str}.docx"


def generate_documents(templates_dir: str, fields: Dict[str, str],
                       doc_labels: List[str], client_folder: str) -> List[str]:
    """Сформировать перечисленные документы в подпапки клиента.

    Возвращает список путей созданных файлов. Документы с отсутствующим шаблоном
    пропускаются (с пометкой в исключении-агрегаторе — здесь просто пропуск).
    """
    fields = dict(fields)
    now = datetime.datetime.now()
    fields.setdefault("TODAY", now.strftime("%d.%m.%Y"))
    fields.setdefault("CREATED_AT", now.strftime("%d.%m.%Y %H:%M"))

    created: List[str] = []
    for label in doc_labels:
        spec = DOC_TEMPLATES.get(label)
        if not spec:
            continue
        template_path = os.path.join(templates_dir, spec["file"])
        if not os.path.exists(template_path):
            continue  # шаблон ещё не положили в сетевую папку — пропускаем
        target_dir = storage.subfolder(client_folder, spec["subfolder"])
        output_path = os.path.join(target_dir, _output_name(label, fields))
        fill_template(template_path, fields, output_path)
        created.append(output_path)
    return created
