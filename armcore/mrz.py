"""
Разбор машиночитаемой зоны паспорта (MRZ).

ТЗ, п.5.1: распознавание MRZ — тип паспорта, код страны, номер паспорта,
фамилия, имя, гражданство, дата рождения, пол, действителен до, при наличии —
персональный (личный идентификационный) номер.

Поддержан формат TD3 (паспорт-книжка): 2 строки по 44 символа. Остальные поля,
которых нет в MRZ (отчество — частично, место рождения, дата выдачи, орган) —
берутся из визуальной зоны (OCR) или вводятся вручную (см. ТЗ, п.5.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from . import transliteration


@dataclass
class MrzResult:
    document_type: str = ""
    country_code: str = ""        # код страны выдачи
    nationality: str = ""         # гражданство
    passport_number: str = ""
    family_latin: str = ""
    given_latin: str = ""         # имя (+ отчество, если есть) латиницей
    birth_date: str = ""          # ДД.ММ.ГГГГ
    sex: str = ""                 # М/Ж
    expiry_date: str = ""         # ДД.ММ.ГГГГ
    personal_id: str = ""
    valid: bool = True            # сошлись ли контрольные цифры
    warnings: List[str] = field(default_factory=list)

    def to_fields(self) -> Dict[str, str]:
        """Привести к ключам полей GUI (как в bary_de.py)."""
        family_cyr = transliteration.transliterate_name(self.family_latin)
        given_cyr = transliteration.transliterate_name(self.given_latin)
        # В странах СНГ в поле «имена» нередко идут имя + отчество.
        parts = given_cyr.split()
        name = parts[0] if parts else ""
        patronymic = " ".join(parts[1:]) if len(parts) > 1 else ""
        fio = " ".join(p for p in (family_cyr, name, patronymic) if p)
        return {
            "FIO": fio,
            "FAMILY": family_cyr,
            "NAME": name,
            "PATRONYMIC": patronymic,
            "BIRTHDAY": self.birth_date,
            "PASSPORT_NUMBER": self.passport_number,
            "DATE_END": self.expiry_date,
            "SEX": self.sex,
            "COUNTRY_CODE": self.country_code,
            "PERSONAL_ID": self.personal_id,
        }


# --- контрольные цифры ICAO Doc 9303 ---
_WEIGHTS = (7, 3, 1)


def _char_value(ch: str) -> int:
    if ch.isdigit():
        return int(ch)
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10
    return 0  # '<' и прочее


def check_digit(data: str) -> int:
    total = 0
    for i, ch in enumerate(data):
        total += _char_value(ch) * _WEIGHTS[i % 3]
    return total % 10


def _parse_date(yymmdd: str, *, future: bool) -> str:
    """YYMMDD -> ДД.ММ.ГГГГ. future=True для срока действия (век 20xx)."""
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return ""
    yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    if future:
        year = 2000 + yy
    else:
        # Дата рождения: если двузначный год больше текущего — это прошлый век.
        current_yy = date.today().year % 100
        year = 2000 + yy if yy <= current_yy else 1900 + yy
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return ""
    return f"{dd:02d}.{mm:02d}.{year:04d}"


def _clean(s: str) -> str:
    return s.replace("<", "").strip()


def parse_td3(line1: str, line2: str) -> MrzResult:
    """Разобрать TD3 (две строки MRZ паспорта)."""
    res = MrzResult()
    line1 = (line1 or "").rstrip("\n").ljust(44, "<")[:44]
    line2 = (line2 or "").rstrip("\n").ljust(44, "<")[:44]

    # Строка 1: код документа, страна, имя.
    res.document_type = _clean(line1[0:2])
    res.country_code = _clean(line1[2:5])
    name_field = line1[5:44]
    if "<<" in name_field:
        family, given = name_field.split("<<", 1)
    else:
        family, given = name_field, ""
    res.family_latin = family.replace("<", " ").strip()
    res.given_latin = given.replace("<", " ").strip()

    # Строка 2: номер, гражданство, даты, пол, перс. номер + контрольные цифры.
    passport = line2[0:9]
    passport_cd = line2[9]
    res.nationality = _clean(line2[10:13])
    birth = line2[13:19]
    birth_cd = line2[19]
    res.sex = {"M": "М", "F": "Ж"}.get(line2[20], "")
    expiry = line2[21:27]
    expiry_cd = line2[27]
    personal = line2[28:42]
    personal_cd = line2[42]
    composite_cd = line2[43]

    res.passport_number = _clean(passport)
    res.personal_id = _clean(personal)
    res.birth_date = _parse_date(birth, future=False)
    res.expiry_date = _parse_date(expiry, future=True)

    # Проверка контрольных цифр (только для цифровых полей).
    def verify(label: str, data: str, cd: str):
        if not cd.isdigit():
            return
        if check_digit(data) != int(cd):
            res.valid = False
            res.warnings.append(f"Не сошлась контрольная цифра: {label}")

    verify("номер паспорта", passport, passport_cd)
    verify("дата рождения", birth, birth_cd)
    verify("срок действия", expiry, expiry_cd)
    if _clean(personal):
        verify("персональный номер", personal, personal_cd)
    composite = passport + passport_cd + birth + birth_cd + expiry + expiry_cd + personal + personal_cd
    verify("итоговая контрольная сумма", composite, composite_cd)

    return res


def parse(mrz_text: str) -> Optional[MrzResult]:
    """Разобрать MRZ из произвольного текста (2 строки TD3).

    Принимает текст с переводами строк, лишними пробелами и т.п.
    Возвращает None, если не удалось выделить две строки по ~44 символа.
    """
    if not mrz_text:
        return None
    lines = [ln.strip().replace(" ", "") for ln in mrz_text.strip().splitlines() if ln.strip()]
    # Строка TD3 — ровно 44 символа. Иногда OCR добавляет лишний '<' в начале
    # (строка становится 45+), что сдвигает все поля. Убираем ведущие '<' у
    # переразмеренных строк, чтобы выравнивание восстановилось.
    norm = []
    for ln in lines:
        while len(ln) > 44 and ln.startswith("<"):
            ln = ln[1:]
        norm.append(ln)
    # Берём две последние «длинные» строки — это и есть MRZ.
    candidates = [ln for ln in norm if len(ln) >= 30]
    if len(candidates) >= 2:
        return parse_td3(candidates[-2], candidates[-1])
    return None
