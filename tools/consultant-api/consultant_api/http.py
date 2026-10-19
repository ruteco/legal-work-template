# -*- coding: utf-8 -*-
"""HTTP-слой: одиночные запросы с паузой между ними, повторы при сбоях сети и 429/5xx."""

import random
import time

import requests

from .errors import NetworkError

# Строка Firefox, установленного у пользователя (158.0, Windows 11; формат фиксирован:
# rv:<версия> и Gecko/20100101). Зашита намеренно: сервер отвергает «python-requests».
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:158.0) Gecko/20100101 Firefox/158.0"
ACCEPT_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
ACCEPT_LANGUAGE = "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3"


class Http:
    def __init__(self, session=None, min_interval=0.8, timeout=120.0):
        self.session = session or requests.Session()
        # присваиваем, а не setdefault: у requests уже есть User-Agent «python-requests», и сервер
        # отвечает на него страницей «Некорректно настроенный веб-обозреватель»
        self.session.headers["User-Agent"] = UA
        self.session.headers["Accept"] = ACCEPT_HTML
        self.session.headers["Accept-Language"] = ACCEPT_LANGUAGE
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0

    def _pace(self):
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.2))

    def request(self, method, url, *, params=None, data=None, headers=None, files=None,
                retries=2, allow_redirects=True, timeout=None):
        last = None
        for attempt in range(retries + 1):
            self._pace()
            try:
                r = self.session.request(method, url, params=params, data=data, headers=headers, files=files,
                                         timeout=timeout or self.timeout, allow_redirects=allow_redirects)
            except requests.RequestException as e:
                last = e
                self._last = time.monotonic()
                time.sleep(1.5 * (attempt + 1))
                continue
            self._last = time.monotonic()
            if r.status_code in (429, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            return r
        raise NetworkError("Сетевая ошибка: %s" % type(last).__name__,
                           hint="Проверьте подключение к интернету и повторите команду.")
