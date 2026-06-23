"""
Сервер АРМ: общая БД клиентов + журнал блокировок папок.

ТЗ, п.1 и п.3.4: единый сервер обрабатывает запросы 4 клиентов одновременно;
обеспечивает блокировку папок (один редактирует — остальные только читают).

Реализация на стандартной библиотеке (http.server + sqlite3), без Flask и без
сторонних зависимостей — по согласованию ставку делаем на десктоп-клиентов, а
сервер держим максимально простым в развёртывании.

Запуск:
    python server/server.py --host 0.0.0.0 --port 8770 --db arm_server.db

API (JSON):
    GET  /api/health
    POST /api/lock     {folder, operator, workstation}   -> {ok} | 409 {ok:false, owner}
    POST /api/unlock   {folder, operator}                 -> {ok}
    GET  /api/locks                                       -> {locks:{...}}
    POST /api/clients  {fields, services, comment, operator} -> {ok, id}
    GET  /api/clients?q=...                               -> {clients:[...]}
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.0.0"
LOCK_TTL_SECONDS = 300

_DB_PATH = "arm_server.db"
_DB_LOCK = threading.Lock()      # сериализуем запись в SQLite


# --------------------------------------------------------------------------- #
#  База данных
# --------------------------------------------------------------------------- #
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS locks (
                folder      TEXT PRIMARY KEY,
                operator    TEXT NOT NULL,
                workstation TEXT,
                ts          REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fio         TEXT,
                passport    TEXT,
                birthday    TEXT,
                country     TEXT,
                date_issue  TEXT,
                issued_by   TEXT,
                services    TEXT,
                comment     TEXT,
                operator    TEXT,
                updated_at  TEXT,
                UNIQUE(fio, passport)
            )
        """)
        conn.commit()


# --------------------------------------------------------------------------- #
#  Логика блокировок
# --------------------------------------------------------------------------- #
def _lock_is_active(row: sqlite3.Row) -> bool:
    return row is not None and (time.time() - row["ts"]) < LOCK_TTL_SECONDS


def acquire_lock(folder: str, operator: str, workstation: str) -> dict:
    with _DB_LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM locks WHERE folder=?", (folder,)).fetchone()
        if _lock_is_active(row) and row["operator"] != operator:
            return {"ok": False, "owner": row["operator"],
                    "workstation": row["workstation"], "status": 409}
        # свободно / просрочено / уже наша — захватываем или продлеваем
        conn.execute(
            "INSERT INTO locks(folder, operator, workstation, ts) VALUES(?,?,?,?) "
            "ON CONFLICT(folder) DO UPDATE SET operator=excluded.operator, "
            "workstation=excluded.workstation, ts=excluded.ts",
            (folder, operator, workstation, time.time()),
        )
        conn.commit()
        return {"ok": True}


def release_lock(folder: str, operator: str) -> dict:
    with _DB_LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM locks WHERE folder=?", (folder,)).fetchone()
        if row and row["operator"] == operator:
            conn.execute("DELETE FROM locks WHERE folder=?", (folder,))
            conn.commit()
        return {"ok": True}


def list_locks() -> dict:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM locks").fetchall()
    locks = {}
    for r in rows:
        if _lock_is_active(r):
            locks[r["folder"]] = {"operator": r["operator"],
                                  "workstation": r["workstation"], "ts": r["ts"]}
    return {"locks": locks}


# --------------------------------------------------------------------------- #
#  Логика клиентов (общая БД)
# --------------------------------------------------------------------------- #
def upsert_client(fields: dict, services: list, comment: str, operator: str) -> dict:
    now = time.strftime("%d.%m.%Y %H:%M")
    with _DB_LOCK, _connect() as conn:
        conn.execute("""
            INSERT INTO clients(fio, passport, birthday, country, date_issue,
                                issued_by, services, comment, operator, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fio, passport) DO UPDATE SET
                birthday=excluded.birthday, country=excluded.country,
                date_issue=excluded.date_issue, issued_by=excluded.issued_by,
                services=excluded.services, comment=excluded.comment,
                operator=excluded.operator, updated_at=excluded.updated_at
        """, (
            fields.get("FIO", ""), fields.get("PASSPORT_NUMBER", ""),
            fields.get("BIRTHDAY", ""), fields.get("COUNTRY_CODE", ""),
            fields.get("DATE_ISSUE", ""), fields.get("ISSUED_BY", ""),
            ", ".join(services), comment, operator, now,
        ))
        conn.commit()
        row = conn.execute(
            "SELECT id FROM clients WHERE fio=? AND passport=?",
            (fields.get("FIO", ""), fields.get("PASSPORT_NUMBER", "")),
        ).fetchone()
    return {"ok": True, "id": row["id"] if row else None}


def search_clients(query: str) -> dict:
    like = f"%{query}%"
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE fio LIKE ? OR passport LIKE ? "
            "ORDER BY updated_at DESC LIMIT 50", (like, like),
        ).fetchall()
    return {"clients": [dict(r) for r in rows]}


# --------------------------------------------------------------------------- #
#  HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = f"ARMServer/{VERSION}"

    def _send(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self._send({"ok": True, "version": VERSION, "time": time.time()})
        if parsed.path == "/api/locks":
            return self._send(list_locks())
        if parsed.path == "/api/clients":
            q = parse_qs(parsed.query).get("q", [""])[0]
            return self._send(search_clients(q))
        return self._send({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        data = self._read_json()
        if parsed.path == "/api/lock":
            res = acquire_lock(data.get("folder", ""), data.get("operator", ""),
                               data.get("workstation", ""))
            status = res.pop("status", 200)
            return self._send(res, status)
        if parsed.path == "/api/unlock":
            return self._send(release_lock(data.get("folder", ""), data.get("operator", "")))
        if parsed.path == "/api/clients":
            return self._send(upsert_client(
                data.get("fields", {}), data.get("services", []),
                data.get("comment", ""), data.get("operator", ""),
            ))
        return self._send({"ok": False, "error": "not found"}, 404)

    def log_message(self, fmt, *args):  # тише в консоли
        pass


def main():
    global _DB_PATH
    ap = argparse.ArgumentParser(description="Сервер АРМ (блокировки + общая БД)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--db", default=os.environ.get("ARM_SERVER_DB", "arm_server.db"))
    args = ap.parse_args()

    _DB_PATH = args.db
    init_db()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ARM server {VERSION} на http://{args.host}:{args.port}  (БД: {_DB_PATH})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
