"""
armcore — ядро АРМ Оператора по приёму иностранных граждан.

Логика, не зависящая от GUI и от конкретного оборудования, вынесена сюда,
чтобы её можно было тестировать без Windows, без PyQt и без сканеров.

Десктоп-клиент (bary_de.py) импортирует эти модули.
"""

__all__ = [
    "config",
    "transliteration",
    "mrz",
    "services",
    "storage",
    "pdf_layout",
    "reports",
    "scanners",
    "locking",
    "serverclient",
    "documents",
    "winio",
    "fonts",
]

__version__ = "1.0.0"
