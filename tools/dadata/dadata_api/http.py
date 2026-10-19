# -*- coding: utf-8 -*-
"""HTTP-клиент DaData: ключ в заголовке, пауза между запросами, повторы при 429/5xx, разбор ошибок, кеш."""

import random
import time

import requests

from . import store
from .errors import AuthError, LimitError, NetworkError, NotFound, UsageError

BASE = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/"
USER_AGENT = "legal-work-template-dadata/1.0 (+https://github.com/ruteco/legal-work-template)"
DAILY_RESET_HINT = ("403 означает: ключ неверен, не подтверждена почта в личном кабинете DaData либо исчерпан суточный лимит "
                    "(10 000 запросов на бесплатном тарифе, сброс в 00:00 по Москве).")


class Client:
    def __init__(self, key=None, min_interval=0.25, timeout=30.0, cache_ttl_hours=24.0):
        self._key = key or store.require_key()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Content-Type": "application/json",
                                     "Accept": "application/json", "Authorization": "Token " + self._key})
        self.min_interval = min_interval
        self.timeout = timeout
        self.cache_ttl_hours = cache_ttl_hours
        self._last = 0.0
        self.last_meta = {}

    def _pace(self):
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.05))

    def post(self, method, body, *, use_cache=True, retries=2):
        """Один вызов метода DaData; возвращает JSON-ответ. Метаданные (из кеша, когда получено) — в last_meta."""
        if use_cache:
            rec = store.cache_get(method, body, self.cache_ttl_hours)
            if rec:
                self.last_meta = {"cached": True, "fetched_at": rec["fetched_at"]}
                return rec["response"]
        last = None
        for attempt in range(retries + 1):
            self._pace()
            try:
                r = self.session.post(BASE + method, json=body, timeout=self.timeout)
            except requests.RequestException as e:
                last = e
                self._last = time.monotonic()
                time.sleep(1.5 * (attempt + 1))
                continue
            self._last = time.monotonic()
            store.count_request()
            if r.status_code in (429, 502, 503, 504) and attempt < retries:
                try:
                    delay = min(float(r.headers.get("Retry-After", "")), 20.0)
                except ValueError:
                    delay = 2.0 * (attempt + 1)
                time.sleep(delay)
                continue
            return self._handle(r, method, body)
        raise NetworkError("Сетевая ошибка при обращении к DaData: %s" % type(last).__name__,
                           hint="Проверьте подключение и повторите.")

    def _handle(self, r, method, body):
        s = r.status_code
        if s == 200:
            try:
                data = r.json()
            except ValueError:
                raise NetworkError("DaData вернула не JSON (код 200).")
            rec = store.cache_put(method, body, data)
            self.last_meta = {"cached": False, "fetched_at": rec["fetched_at"]}
            return data
        detail = (r.text or "")[:200].replace("\n", " ")
        if s == 401:
            raise AuthError("DaData не приняла ключ (401).", hint="Проверьте ключ в личном кабинете и сохраните заново: auth set.")
        if s == 403:
            raise LimitError("Доступ запрещён (403): %s" % detail, code="forbidden", hint=DAILY_RESET_HINT)
        if s == 429:
            raise LimitError("Слишком много запросов (429).", code="rate_limit",
                             hint="Лимит: 30 запросов в секунду и 60 новых соединений в минуту с одного IP; повторите позже.")
        if s == 404:
            raise NotFound("Метод не найден: %s" % method)
        if s in (400, 405, 413):
            raise UsageError("DaData отклонила запрос (%d): %s" % (s, detail))
        raise NetworkError("Ответ DaData %d: %s" % (s, detail), code="server_error")
