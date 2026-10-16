"""Thin client for the CRM sandbox API."""
import time

import requests

from . import config


class CRMError(RuntimeError):
    pass


class CRMClient:
    def __init__(self, token: str | None = None, base: str = config.API_BASE):
        self.base = base
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token or config.require_token()}"

    def _request(self, method: str, path: str, **kwargs):
        for attempt in range(3):
            resp = self.session.request(method, f"{self.base}{path}", timeout=30, **kwargs)
            if resp.status_code >= 500 and attempt < 2:
                time.sleep(1 + attempt)
                continue
            if resp.status_code >= 400:
                raise CRMError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise CRMError(f"{method} {path} failed after retries")

    def _paginate(self, path: str, params: dict | None = None) -> list[dict]:
        params = dict(params or {}, page_size=100)
        rows, page = [], 1
        while True:
            body = self._request("GET", path, params={**params, "page": page})
            rows.extend(body["data"])
            if not body["data"] or len(rows) >= body.get("total", 0):
                return rows
            page += 1

    def list_accounts(self) -> list[dict]:
        return self._paginate("/accounts")

    def list_contacts(self) -> list[dict]:
        return self._paginate("/contacts")

    def get_account(self, account_id: str) -> dict:
        return self._request("GET", f"/accounts/{account_id}")

    def update_account(self, account_id: str, fields: dict) -> dict:
        return self._request("PATCH", f"/accounts/{account_id}", json=fields)

    def create_account(self, fields: dict) -> dict:
        return self._request("POST", "/accounts", json=fields)
