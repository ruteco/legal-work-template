# -*- coding: utf-8 -*-
"""HTTP-слой: честный User-Agent, пауза между запросами, повторы, проверка robots.txt."""

import os
import random
import time
from urllib.parse import urlparse

import requests

from .errors import NetworkError
from .robots import Robots

PRODUCT = "legal-work-template-science-search/1.0"


def user_agent():
    """Honest UA. Если пользователь задал SCIENCE_MAILTO, API получают «вежливый» контакт (быстрее лимиты)."""
    mailto = os.environ.get("SCIENCE_MAILTO")
    return "%s (+https://github.com/ruteco/legal-work-template%s)" % (PRODUCT, "; mailto:" + mailto if mailto else "")


class Http:
    def __init__(self, min_interval=0.5, timeout=45.0):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent()
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0
        self._robots = {}

    def _pace(self):
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.15))

    def get(self, url, *, params=None, headers=None, stream=False, retries=2, allow_redirects=True, timeout=None):
        last = None
        for attempt in range(retries + 1):
            self._pace()
            try:
                r = self.session.get(url, params=params, headers=headers, stream=stream, allow_redirects=allow_redirects,
                                     timeout=timeout or self.timeout)
            except requests.RequestException as e:
                last = e
                self._last = time.monotonic()
                time.sleep(1.5 * (attempt + 1))
                continue
            self._last = time.monotonic()
            if r.status_code in (429, 502, 503, 504) and attempt < retries:
                try:
                    delay = min(float(r.headers.get("Retry-After", "")), 20.0)
                except ValueError:
                    delay = 3.0 * (attempt + 1)
                r.close()
                time.sleep(delay)
                continue
            return r
        raise NetworkError("Сетевая ошибка при обращении к %s: %s" % (urlparse(url).netloc, type(last).__name__),
                           hint="Проверьте подключение и повторите.")

    # ----- robots.txt ----------------------------------------------------------------------------------------

    def robots_allowed(self, url):
        """Можно ли автоматически загружать url по robots.txt его сайта. Недоступный robots.txt = нельзя
        (кроме 4xx-«файла нет», это стандартное «можно всё»)."""
        p = urlparse(url)
        base = "%s://%s" % (p.scheme, p.netloc)
        if base not in self._robots:
            try:
                r = self.get(base + "/robots.txt", retries=1, timeout=15)
                if r.status_code in (401, 403) or r.status_code >= 500:
                    rp = Robots(disallow_all=True)
                elif r.status_code >= 400:
                    rp = Robots(allow_all=True)
                else:
                    rp = Robots(r.text)
            except NetworkError:
                rp = Robots(disallow_all=True)
            self._robots[base] = rp
        return self._robots[base].can_fetch(PRODUCT, url)
