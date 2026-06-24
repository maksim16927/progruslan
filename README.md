# АРМ Оператора по приёму иностранных граждан

Десктоп-приложение (PyQt6) для автоматизации рабочего места оператора по приёму
документов у иностранных граждан: сканирование паспорта (**Regula 7017**) и
сшитых документов (**Kodak SceyeX**), транслитерация, формирование комплекта
документов, разворотов и двух Excel-реестров. Реализация по техническому заданию
«АРМ Оператора по приёму иностранных граждан».

> Развитие исходного прототипа `bary_de.py` (Sokrat Helper). Вся предметная
> логика вынесена в пакет `armcore/`; интерфейс — в `bary_de.py`; общий сервер
> блокировок и БД — в `server/`.

## Возможности (по ТЗ)

| Раздел ТЗ | Реализация |
|---|---|
| Чтение MRZ паспорта | `armcore/mrz.py` (формат TD3 + контрольные цифры ICAO 9303) |
| Транслитерация lat→cyr, ГОСТ 7.79-2000 (сист. Б), ручная правка | `armcore/transliteration.py` |
| Согласие на ПД (формируется первым) + папка клиента | `armcore/documents.py`, `armcore/storage.py` |
| Чек-лист услуг; договор → договор + акт | `armcore/services.py` |
| Перевод паспорта (сканы страниц + шаблон) | сканер `scan_pages` + шаблоны переводов |
| Развороты по 4 стр. на лист (2×2, A4 альбом, 300 dpi) | `armcore/pdf_layout.py` |
| Сшитые документы с Kodak (многостраничный PDF) | сканер `scan_document` + `images_to_pdf` |
| Качество сканов 300 dpi, цветной PDF | `armcore/config.py`, `armcore/scanners.py` |
| Структура `X:\Archive\Фамилия_Имя_Отчество_ДДММГГГГ\` | `armcore/storage.py` |
| Многопользовательский режим, блокировка папок | `armcore/locking.py` + `server/server.py` |
| Шаблоны .docx в сетевой папке, печать | `armcore/documents.py`, `armcore/winio.py` |
| Два Excel-реестра (обучение / прочие) | `armcore/reports.py` |
| Еженощный бэкап Archive | `server/backup.py` |

## Структура проекта

```
bary_de.py              десктоп-клиент (GUI, PyQt6) — точка входа АРМ
armcore/                ядро (без GUI, без железа — тестируемо отдельно)
  config.py             пути, адрес сервера, dpi, имя оператора
  transliteration.py    lat→cyr (ГОСТ 7.79-2000, система Б)
  mrz.py                разбор MRZ (TD3)
  services.py           перечень услуг, правило «договор → акт»
  storage.py            структура X:\Archive\..., состав папки клиента
  pdf_layout.py         развороты 2×2 и сборка PDF (300 dpi)
  reports.py            два Excel-реестра
  scanners.py           Regula/Kodak (каркас под SDK) + MockScanner
  documents.py          заполнение .docx-шаблонов (<<KEY>>)
  fonts.py              подбор кириллического TrueType-шрифта для PDF
  locking.py            блокировка папок (сервер + lock-файлы)
  serverclient.py       HTTP-клиент к серверу
  winio.py              печать и docx→pdf (Windows + fallback)
server/
  server.py             общий сервер: блокировки + БД клиентов (stdlib + SQLite)
  backup.py             еженощный бэкап Archive
tests/test_core.py      юнит-тесты ядра
mock_scans/             демо-изображения для MockScanner (без оборудования)
```

## Установка

```bash
pip install -r requirements.txt
```

На рабочих местах (Windows) дополнительно ставятся `pywin32` и `comtypes`
(см. requirements.txt), а также драйверы/SDK сканеров.

### Распознавание паспорта из файла-скана (OCR) на Windows

Авто-распознавание MRZ требует **Tesseract OCR** — на Windows его нужно
поставить отдельно (на Linux/macOS — через пакетный менеджер):

1. Скачать установщик: https://github.com/UB-Mannheim/tesseract/wiki
   (`tesseract-ocr-w64-setup-*.exe`) и установить.
2. Установить Python-пакеты: `pip install passporteye pytesseract opencv-python-headless`.
3. Программа сама ищет `tesseract.exe` в стандартных путях
   (`C:\Program Files\Tesseract-OCR\`). Если установили в другое место —
   задайте путь переменной окружения `ARM_TESSERACT`, например:
   `set ARM_TESSERACT=D:\Tools\Tesseract-OCR\tesseract.exe`.

Если при выборе скана появляется «MRZ распознать не удалось», программа
подскажет, какого компонента не хватает (tesseract / passporteye / pytesseract).

## Запуск

**Сервер** (один на сеть, на файловом сервере):

```bash
python server/server.py --host 0.0.0.0 --port 8770 --db arm_server.db
```

**Клиент (АРМ)** — на каждом из 4 рабочих мест:

```bash
python bary_de.py
```

**Бэкап** (в планировщик задач Windows, ежедневно ночью):

```bash
python server/backup.py --src X:\Archive --dest D:\Backups\Archive --keep 14 --mode zip
```

## Конфигурация

Настройки — в `armcore/config.py`. Переопределяются файлом `arm_config.json`
рядом с программой или переменными окружения.

> **Где взять `arm_config.json`:** он НЕ хранится в гите (в нём локальные пути
> конкретной машины). Скопируйте шаблон `arm_config.example.json` → `arm_config.json`
> и поправьте пути под себя. На Windows (cmd): `copy arm_config.example.json arm_config.json`.

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `ARM_ARCHIVE_ROOT` | корень хранилища | `X:\Archive` |
| `ARM_TEMPLATES_DIR` | папка .docx-шаблонов | `X:\Archive\_templates` |
| `ARM_SERVER_URL` | адрес сервера | `http://127.0.0.1:8770` |
| `ARM_OPERATOR` | имя оператора (для блокировок/реестров) | имя пользователя ОС |
| `ARM_MOCK_SCANNERS` | `1` — работать без железа (mock) | `1` |

> Пока `mock_scanners=1`, кнопки сканирования берут изображения из `mock_scans/`
> (см. `armcore/scanners.py`). Для реального оборудования установите `0` и
> реализуйте отмеченные `TODO(SDK)` / `TODO(TWAIN)` в `armcore/scanners.py`.

## Шаблоны документов

Кладутся в `ARM_TEMPLATES_DIR`. Плейсхолдеры в тексте — вида `<<KEY>>`:

`<<FIO>>`, `<<FAMILY>>`, `<<NAME>>`, `<<PATRONYMIC>>`, `<<BIRTHDAY>>`,
`<<PASSPORT_NUMBER>>`, `<<DATE_ISSUE>>`, `<<DATE_END>>`, `<<REG_ADDRESS>>`,
`<<BIRTHPLACE>>`, `<<SEX>>`, `<<ISSUED_BY>>`, `<<COUNTRY_CODE>>`,
`<<PERSONAL_ID>>`, `<<TODAY>>`, `<<CREATED_AT>>`.

Имена файлов шаблонов — см. `armcore/documents.py` (`DOC_TEMPLATES`):
`согласие.docx`, `договор_обучение.docx`, `договор_сопровождение.docx`,
`акт.docx`, `полис_дмс.docx`, `перевод_гу.docx`, `перевод_пвс.docx`.

## Тесты

```bash
python -m unittest tests.test_core -v
```

## Реальный сканер Regula 7017 (Passport Reader SDK, COM)

Поставка **Regula Passport Reader SDK** (`PasspR40.dll` и др.) регистрирует
COM-компонент **`READERDEMO.RegulaReader`**. Программа подключается к нему из
Python через **pywin32** (`win32com.client`). SDK сам управляет устройством и
распознаёт документ (встроенный модуль): MRZ, визуальная зона (VIZ), портрет.
Реализация — `RegulaScanner` в `armcore/scanners.py`.

Настройка на рабочем месте со сканером (Windows):

1. Установить Regula Passport Reader SDK (COM-объект `READERDEMO.RegulaReader`
   должен быть зарегистрирован установщиком; иначе `regsvr32`).
2. Установить pywin32: `pip install pywin32`.
3. В `arm_config.json` поставить `"mock_scanners": false`.
4. Проверить связку **диагностическим скриптом** (без GUI):

   ```bat
   rem захват с устройства (положите паспорт на сканер):
   py tools\regula_selftest.py

   rem либо распознать готовый скан-файл (без устройства):
   py tools\regula_selftest.py C:\путь\к\скану.jpg
   ```

   Скрипт создаёт COM-объект, делает захват/распознавание, печатает MRZ и поля,
   сохраняет портрет в `tools\regula_selftest_out`. Весь вывод и файлы —
   прислать разработчику для финальной сверки под вашу версию SDK.

> Коды полей и методы (`GetTextFieldByType`, `GetMRZLines`,
> `GetReaderGraphicsBitmapByFieldType`, `DoProcessImage`) взяты из поставки SDK
> (READERDEMO_TLB, PasspR.h). Между версиями возможны отличия — сверяем по
> выводу `regula_selftest.py`.

## Текущие ограничения

- **Сканеры Regula 7017 и Kodak SceyeX** подключены как *каркас под реальный
  SDK*: интерфейс и mock готовы, вызовы железа помечены `TODO` в
  `armcore/scanners.py`. Без оборудования работает режим `mock_scanners`.
- **Печать и docx→pdf** на Windows используют MS Word/`pywin32`; на других ОС —
  LibreOffice/`lpr` (для разработки).
