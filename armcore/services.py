"""
Перечень услуг и правила формирования комплекта документов.

ТЗ, п.3.1: «появляется окошечко с перечнем услуг, напротив нужных можно
поставить галочку (в результате на каждого клиента формируется список).
При выборе договора на обучении или договора на сопровождение, автоматически
формируется не только договор, но и акт».

ТЗ, п.9: два Excel-реестра —
  1) только те, кто проходит обучение (договор и акт на обучение);
  2) все остальные.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


# Виды пунктов чек-листа.
KIND_CONTRACT = "contract"   # договор (влечёт акт)
KIND_DOC = "doc"             # формируемый документ (перевод и т.п.)
KIND_SCAN = "scan"           # скан-задача (прикладывается в папку клиента)
KIND_OTHER = "other"         # прочее с комментарием


@dataclass(frozen=True)
class Service:
    label: str
    kind: str
    needs_comment: bool = False


# Полный перечень услуг строго по ТЗ (раздел «Услуги»).
SERVICES: List[Service] = [
    Service("Договор на обучение", KIND_CONTRACT),
    Service("Договор сопровождение", KIND_CONTRACT),
    Service("Скан паспорта", KIND_SCAN),
    Service("Скан миграционной карты", KIND_SCAN),
    Service("Скан регистрации", KIND_SCAN),
    Service("Скан гринкарты", KIND_SCAN),
    Service("Скан полиса", KIND_SCAN),
    Service("Скан справки ВИЧ", KIND_SCAN),
    Service("Скан справки инф.заб.", KIND_SCAN),
    Service("Скан справки наркотики", KIND_SCAN),
    Service("Скан чеков об оплате патента", KIND_SCAN),
    Service("Скан печатей в паспорте", KIND_SCAN),
    Service("Скан патента", KIND_SCAN),
    Service("Скан перевода паспорта", KIND_SCAN),
    Service("Полис ДМС", KIND_DOC),
    Service("Перевод паспорта ГУ", KIND_DOC),
    Service("Перевод паспорта ПВС", KIND_DOC),
    Service("Прочее", KIND_OTHER, needs_comment=True),
]

SERVICE_LABELS: List[str] = [s.label for s in SERVICES]

# Договоры, при которых автоматически формируется акт.
CONTRACTS_WITH_ACT = {"Договор на обучение", "Договор сопровождение"}

# Услуги, относящие клиента в реестр «обучение» (первый Excel).
STUDY_SERVICES = {"Договор на обучение"}

ACT_DOCUMENT = "Акт"


def get_service(label: str) -> Service | None:
    for s in SERVICES:
        if s.label == label:
            return s
    return None


def documents_for_selection(selected: List[str]) -> List[str]:
    """По отмеченным услугам вернуть список ДОКУМЕНТОВ к формированию (.docx).

    Договор -> договор + акт. Документы-переводы и полис ДМС добавляются как есть.
    Скан-задачи документами не являются и сюда не попадают.
    """
    docs: List[str] = []
    for label in selected:
        svc = get_service(label)
        if svc is None:
            continue
        if svc.kind in (KIND_CONTRACT, KIND_DOC):
            if label not in docs:
                docs.append(label)
            if label in CONTRACTS_WITH_ACT and ACT_DOCUMENT not in docs:
                docs.append(ACT_DOCUMENT)
    return docs


def scan_tasks_for_selection(selected: List[str]) -> List[str]:
    """Вернуть отмеченные скан-задачи (что нужно отсканировать и приложить)."""
    return [label for label in selected
            if (s := get_service(label)) is not None and s.kind == KIND_SCAN]


def is_study_client(selected: List[str]) -> bool:
    """Идёт ли клиент в реестр «обучение» (первый Excel)."""
    return any(label in STUDY_SERVICES for label in selected)
