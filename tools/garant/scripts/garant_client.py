"""
Клиент официального API системы ГАРАНТ (https://api.garant.ru/v1).

Основано на docs/api-spec-summary.md (выжимка из docs/garant_api_v1.7.pdf).

СТАТУС: не протестировано. На момент написания нет договора/токена доступа —
ни один метод ни разу не вызывался против реального сервера ГАРАНТ.
"""

from __future__ import annotations

import os
from typing import Any

import requests

BASE_URL = "https://api.garant.ru/v1"


class GarantAPIError(RuntimeError):
    def __init__(self, status_code: int, payload: Any):
        super().__init__(f"Garant API error {status_code}: {payload}")
        self.status_code = status_code
        self.payload = payload


class GarantClient:
    def __init__(self, token: str | None = None, base_url: str = BASE_URL, timeout: float = 30.0):
        self.token = token or os.environ.get("GARANT_API_TOKEN")
        if not self.token:
            raise RuntimeError(
                "GARANT_API_TOKEN не задан. Токен выдаётся в личном кабинете "
                "администратора комплекта ГАРАНТ после покупки пакета LegalTech API."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        if not resp.ok:
            try:
                payload = resp.json()
            except ValueError:
                payload = resp.text
            raise GarantAPIError(resp.status_code, payload)
        return resp

    # --- Поиск и фрагменты ------------------------------------------------

    def search(
        self,
        text: str,
        *,
        is_query: bool = False,
        count: int = 30,
        kind: list[str] | None = None,
        sort: int = 0,
        sort_order: int = 0,
    ) -> list[dict]:
        """POST /search — поиск документов по тексту или языку запросов (isQuery=True)."""
        body = {
            "text": text,
            "isQuery": is_query,
            "count": count,
            "kind": kind or [],
            "sort": sort,
            "sortOrder": sort_order,
        }
        resp = self._request("POST", "/search", json=body)
        return resp.json().get("documents", [])

    def get_snippets(self, text: str, topic: int, correspondent: dict | None = None) -> list[dict]:
        """POST /snippets — релевантные фрагменты документа под текст запроса."""
        body: dict[str, Any] = {"text": text, "topic": topic}
        if correspondent:
            body["correspondent"] = correspondent
        resp = self._request("POST", "/snippets", json=body)
        return resp.json().get("snippets", [])

    def find_hyperlinks(self, text: str, base_url: str = "https://internet.garant.ru") -> str:
        """POST /find-hyperlinks — простановка ссылок на упомянутые документы (текст ≤ 20 МБ)."""
        body = {"text": text, "baseUrl": base_url}
        resp = self._request("POST", "/find-hyperlinks", json=body)
        return resp.json()["text"]

    # --- Текст документа ----------------------------------------------------

    def download_rtf(self, topic: int) -> bytes:
        """GET /topic/{topic}/download — документ целиком, RTF."""
        return self._request("GET", f"/topic/{topic}/download").content

    def download_odt(self, topic: int) -> bytes:
        """GET /topic/{topic}/download-odt — документ целиком, ODT."""
        return self._request("GET", f"/topic/{topic}/download-odt").content

    def download_pdf(self, topic: int) -> bytes:
        """GET /topic/{topic}/download-pdf — документ целиком, PDF."""
        return self._request("GET", f"/topic/{topic}/download-pdf").content

    def get_html(self, topic: int) -> list[dict]:
        """GET /topic/{topic}/html — документ постранично, HTML-фрагменты."""
        resp = self._request("GET", f"/topic/{topic}/html")
        return resp.json().get("items", [])

    def get_entry_html(self, topic: int, entry: int) -> str:
        """GET /topic/{topic}/entry/{entry}/html — конкретный пункт/абзац документа."""
        resp = self._request("GET", f"/topic/{topic}/entry/{entry}/html")
        return resp.json()["body"]

    def get_formula(self, text_b64: str, fmt: str = "png") -> bytes:
        """GET /formula — изображение формулы (text — то же base64, что в src img-тега)."""
        resp = self._request("GET", "/formula", params={"text": text_b64, "fmt": fmt})
        return resp.content

    # --- Метаданные и мониторинг ---------------------------------------------

    def get_topic_info(self, topic: int) -> dict:
        """GET /topic/{topic} — метаданные документа (лимит 300 запросов/месяц)."""
        return self._request("GET", f"/topic/{topic}").json()

    def find_modified(self, topics: list[int], mod_date: str, need_events: bool = False) -> dict:
        """POST /find-modified — проверка изменений по списку документов (до 100 за вызов)."""
        body = {"topics": topics, "modDate": mod_date, "needEvents": need_events}
        return self._request("POST", "/find-modified", json=body).json()

    def block_on_control_changed(self, from_date: str, url_array: list[str]) -> list[dict]:
        """POST /block-on-control/changed — статус изменений по конкретным пунктам документов."""
        body = {"fromDate": from_date, "urlArray": url_array}
        resp = self._request("POST", "/block-on-control/changed", json=body)
        return resp.json().get("urlArray", [])

    # --- Лента ПРАЙМ -------------------------------------------------------------

    def get_prime_categories(self) -> list[dict]:
        """GET /prime — дерево категорий новостей ленты ПРАЙМ."""
        return self._request("GET", "/prime").json().get("categories", [])

    def get_prime_news(self, categories: list[int], from_date: str, to_date: str | None = None) -> list[dict]:
        """POST /prime/create-news — новости по категориям (не старше 1 года)."""
        body: dict[str, Any] = {"categories": categories, "fromDate": from_date}
        if to_date:
            body["toDate"] = to_date
        resp = self._request("POST", "/prime/create-news", json=body)
        return resp.json().get("news", [])
