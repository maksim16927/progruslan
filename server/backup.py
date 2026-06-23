"""
Еженощный бэкап папки Archive.

ТЗ, п.6: «На сервере еженощный бекап папки Archive (реализовать скрипт)».

Скрипт делает датированную копию (ZIP или зеркальное копирование), хранит
последние N бэкапов, ведёт лог. Ставится в планировщик задач Windows (или cron)
на ночное время, например ежедневно в 02:00.

Запуск:
    python server/backup.py --src X:\\Archive --dest D:\\Backups --keep 14 --mode zip
"""
from __future__ import annotations

import argparse
import os
import shutil
import time


def _log(dest: str, message: str):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}"
    print(line)
    try:
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "backup.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def make_backup(src: str, dest: str, mode: str = "zip") -> str:
    if not os.path.isdir(src):
        raise FileNotFoundError(f"Источник не найден: {src}")
    os.makedirs(dest, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.join(dest, f"Archive_{stamp}")

    if mode == "zip":
        # make_archive сам добавит расширение .zip
        path = shutil.make_archive(base, "zip", root_dir=src)
    elif mode == "copy":
        path = base
        shutil.copytree(src, path)
    else:
        raise ValueError("mode должен быть 'zip' или 'copy'")
    return path


def prune_old(dest: str, keep: int):
    """Оставить только последние keep бэкапов (по времени создания)."""
    if keep <= 0:
        return
    items = []
    for name in os.listdir(dest):
        if name.startswith("Archive_"):
            full = os.path.join(dest, name)
            items.append((os.path.getmtime(full), full))
    items.sort(reverse=True)  # новые первыми
    for _, full in items[keep:]:
        try:
            if os.path.isdir(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
            _log(dest, f"Удалён старый бэкап: {os.path.basename(full)}")
        except OSError as e:
            _log(dest, f"Не удалось удалить {full}: {e}")


def main():
    ap = argparse.ArgumentParser(description="Еженощный бэкап папки Archive")
    ap.add_argument("--src", default=os.environ.get("ARM_ARCHIVE_ROOT", r"X:\Archive"))
    ap.add_argument("--dest", default=os.environ.get("ARM_BACKUP_DIR", r"D:\Backups\Archive"))
    ap.add_argument("--keep", type=int, default=14, help="сколько последних бэкапов хранить")
    ap.add_argument("--mode", choices=["zip", "copy"], default="zip")
    args = ap.parse_args()

    _log(args.dest, f"Старт бэкапа: {args.src} -> {args.dest} (mode={args.mode})")
    try:
        path = make_backup(args.src, args.dest, args.mode)
        size_mb = (os.path.getsize(path) / 1e6) if os.path.isfile(path) else 0
        _log(args.dest, f"Готово: {os.path.basename(path)}"
                        + (f" ({size_mb:.1f} МБ)" if size_mb else ""))
        prune_old(args.dest, args.keep)
    except Exception as e:  # noqa: BLE001 — в ночном скрипте логируем любую ошибку
        _log(args.dest, f"ОШИБКА бэкапа: {e}")
        raise


if __name__ == "__main__":
    main()
