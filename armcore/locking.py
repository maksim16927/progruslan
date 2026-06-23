"""
Блокировка папок клиентов для многопользовательского режима.

ТЗ, п.3.4: «при работе одного оператора с данными клиента "Иванов", для других
операторов папка "Иванов" доступна только для чтения».

Механизм: основной — через сервер (общий журнал блокировок), запасной — через
lock-файл в самой папке клиента (если сервер недоступен). У блокировки есть TTL,
который клиент периодически продлевает (heartbeat); «зависшая» блокировка
освобождается автоматически.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .serverclient import ServerClient, ServerUnavailable


LOCK_TTL_SECONDS = 300          # блокировка считается просроченной через 5 минут
LOCK_FILENAME = ".arm.lock"


@dataclass
class LockStatus:
    acquired: bool
    owner: Optional[str] = None         # оператор, держащий блокировку
    workstation: Optional[str] = None
    via: str = ""                       # "server" | "local"
    read_only: bool = False             # True, если папку держит другой оператор


class _LocalLock:
    """Запасная блокировка через lock-файл в папке клиента."""

    def __init__(self, folder_path: str, operator: str, workstation: str):
        self.folder_path = folder_path
        self.operator = operator
        self.workstation = workstation
        self.lock_path = os.path.join(folder_path, LOCK_FILENAME)

    def _read(self) -> Optional[dict]:
        try:
            with open(self.lock_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def _write(self):
        data = {"operator": self.operator, "workstation": self.workstation, "ts": time.time()}
        with open(self.lock_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def acquire(self) -> LockStatus:
        info = self._read()
        if info:
            fresh = (time.time() - info.get("ts", 0)) < LOCK_TTL_SECONDS
            if fresh and info.get("operator") != self.operator:
                return LockStatus(False, owner=info.get("operator"),
                                  workstation=info.get("workstation"),
                                  via="local", read_only=True)
        os.makedirs(self.folder_path, exist_ok=True)
        self._write()
        return LockStatus(True, owner=self.operator, via="local")

    def refresh(self):
        info = self._read()
        if not info or info.get("operator") == self.operator:
            self._write()

    def release(self):
        info = self._read()
        if info and info.get("operator") == self.operator:
            try:
                os.remove(self.lock_path)
            except OSError:
                pass


class FolderGuard:
    """Высокоуровневая блокировка папки клиента.

    Пытается работать через сервер; при недоступности — через lock-файл.
    Используется как контекстный менеджер:

        guard = FolderGuard(cfg, folder_id, folder_path)
        status = guard.acquire()
        if status.read_only:
            ... показать «папка занята оператором X, режим только чтение»
        ...
        guard.release()
    """

    def __init__(self, cfg: Config, folder_id: str, folder_path: str):
        self.cfg = cfg
        self.folder_id = folder_id              # стабильный идентификатор (имя папки)
        self.folder_path = folder_path
        self._client = ServerClient(cfg.server_url, cfg.operator, cfg.workstation)
        self._local = _LocalLock(folder_path, cfg.operator, cfg.workstation)
        self._mode = None                        # "server" | "local"
        self.status: Optional[LockStatus] = None

    def acquire(self) -> LockStatus:
        try:
            res = self._client.acquire_lock(self.folder_id)
            if res.get("ok"):
                self._mode = "server"
                self.status = LockStatus(True, owner=self.cfg.operator, via="server")
            else:
                self._mode = "server"
                self.status = LockStatus(
                    False, owner=res.get("owner"),
                    workstation=res.get("workstation"),
                    via="server", read_only=True,
                )
            return self.status
        except ServerUnavailable:
            self._mode = "local"
            self.status = self._local.acquire()
            return self.status

    def refresh(self):
        """Продлить блокировку (heartbeat). Вызывать по таймеру из GUI."""
        if self._mode == "server":
            try:
                self._client.acquire_lock(self.folder_id)
            except ServerUnavailable:
                pass
        elif self._mode == "local":
            self._local.refresh()

    def release(self):
        if self._mode == "server":
            try:
                self._client.release_lock(self.folder_id)
            except ServerUnavailable:
                pass
        elif self._mode == "local":
            self._local.release()
        self.status = None

    def __enter__(self) -> LockStatus:
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
