"""
Клиент открытого API publication.pravo.gov.ru (Официальное опубликование
правовых актов, часть pravo.gov.ru).

Публичный API, только чтение, авторизация не требуется. Документация:
http://publication.pravo.gov.ru/help (выжимка — ../docs/api-spec-summary.md).

Проверено вживую 24.07.2026: все методы ниже вызывались против реального
сервера и вернули корректные данные (см. worklogs/recent.md).
"""

from __future__ import annotations

from typing import Any

import requests

BASE_URL = "http://publication.pravo.gov.ru"


class PravoAPIError(RuntimeError):
    def __init__(self, status_code: int, payload: Any):
        super().__init__(f"pravo.gov.ru API error {status_code}: {payload}")
        self.status_code = status_code
        self.payload = payload


class PravoClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params, timeout=self.timeout)
        if not resp.ok:
            raise PravoAPIError(resp.status_code, resp.text)
        return resp

    # --- Справочники ------------------------------------------------------

    def get_public_blocks(self, parent: str | None = None) -> list[dict]:
        """GET /api/PublicBlocks — Блоки публикации (и подблоки, если задан parent)."""
        params = {"parent": parent} if parent else None
        return self._get("/api/PublicBlocks", params).json()

    def get_categories(self, block: str) -> list[dict]:
        """GET /api/Categories — Категории принявших органов по Блоку публикации."""
        return self._get("/api/Categories", {"block": block}).json()

    def get_signatory_authorities(self, block: str | None = None, category: str | None = None) -> list[dict]:
        """GET /api/SignatoryAuthorities — список Принявших органов."""
        params = {k: v for k, v in {"block": block, "category": category}.items() if v}
        return self._get("/api/SignatoryAuthorities", params).json()

    def get_document_types(
        self,
        block: str | None = None,
        category: str | None = None,
        signatory_authority_id: str | None = None,
    ) -> list[dict]:
        """GET /api/DocumentTypes — список Видов документов."""
        params = {
            k: v
            for k, v in {
                "block": block,
                "category": category,
                "SignatoryAuthorityId": signatory_authority_id,
            }.items()
            if v
        }
        return self._get("/api/DocumentTypes", params).json()

    def get_block_statistics(self, period: str = "daily") -> list[dict]:
        """GET /api/BlockStatistics/{period} — статистика опубликования (daily|weekly|monthly)."""
        return self._get(f"/api/BlockStatistics/{period}").json()

    # --- Поиск и документы --------------------------------------------------

    def search(self, **params: Any) -> dict:
        """GET /api/Documents — поиск документов (НПА) с пагинацией.

        Поддерживаемые параметры (все необязательные): Block, Category,
        SignatoryAuthorityId, DocumentTypeId, EoNumber, PeriodType, Date,
        DocumentDateFrom, DocumentDateTo, Name, ComplexName, NumberSearchType,
        Number, JdRegNumber, JdRegDateFrom, JdRegDateTo, PublishDateFrom,
        PublishDateTo, DocumentText, PageSize, Index, SortedBy, SortDestination.
        См. docs/api-spec-summary.md.
        """
        params = {k: v for k, v in params.items() if v is not None}
        return self._get("/api/Documents", params).json()

    def get_document(self, eo_number: str) -> dict:
        """GET /api/Document?eoNumber= — расширенные метаданные одного документа."""
        return self._get("/api/Document", {"eoNumber": eo_number}).json()

    def download_pdf(self, eo_number: str) -> bytes:
        """GET /file/pdf?eoNumber= — сам файл документа, PDF. Без авторизации."""
        url = f"{self.base_url}/file/pdf"
        resp = self.session.get(url, params={"eoNumber": eo_number}, timeout=self.timeout)
        if not resp.ok:
            raise PravoAPIError(resp.status_code, resp.text)
        return resp.content
