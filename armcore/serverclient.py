"""
HTTP-клиент к серверу АРМ (блокировки папок + общая БД клиентов).

Используется только stdlib (urllib) — никаких внешних зависимостей и без Flask
на стороне клиента. Все методы при недоступности сервера бросают
ServerUnavailable, чтобы вызывающий код мог уйти в локальный режим (lock-файлы).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional


class ServerUnavailable(RuntimeError):
    pass


class ServerClient:
    def __init__(self, base_url: str, operator: str, workstation: str, timeout: float = 4.0):
        self.base_url = base_url.rstrip("/")
        self.operator = operator
        self.workstation = workstation
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"ok": False, "error": body}
            parsed["status"] = e.code
            return parsed
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise ServerUnavailable(str(e)) from e

    # --- здоровье ---
    def health(self) -> dict:
        return self._request("GET", "/api/health")

    # --- блокировки папок ---
    def acquire_lock(self, folder: str) -> dict:
        """Захватить/обновить блокировку. {ok:true} или {ok:false, owner:...}."""
        return self._request("POST", "/api/lock", {
            "folder": folder, "operator": self.operator, "workstation": self.workstation,
        })

    def release_lock(self, folder: str) -> dict:
        return self._request("POST", "/api/unlock", {
            "folder": folder, "operator": self.operator,
        })

    def list_locks(self) -> Dict[str, dict]:
        return self._request("GET", "/api/locks").get("locks", {})

    # --- общая БД клиентов ---
    def upsert_client(self, fields: dict, selected: List[str], comment: str = "") -> dict:
        return self._request("POST", "/api/clients", {
            "fields": fields, "services": selected, "comment": comment,
            "operator": self.operator,
        })

    def search_clients(self, query: str) -> List[dict]:
        res = self._request("GET", f"/api/clients?q={urllib.parse.quote(query)}")
        return res.get("clients", [])
