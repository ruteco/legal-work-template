# -*- coding: utf-8 -*-
"""Разбор robots.txt по RFC 9309: группы по User-agent, шаблоны с `*` и `$`, побеждает самое длинное совпадение
(при равенстве — Allow). Стандартный urllib.robotparser шаблоны `*` в путях не понимает и разрешает то, что сайт
запрещает (например, `Disallow: /article/n/*/pdf`), поэтому используется собственный разбор."""

import re
from urllib.parse import unquote, urlparse


def _pattern(path):
    """Шаблон пути robots.txt -> скомпилированное регулярное выражение (совпадение с начала пути)."""
    anchored = path.endswith("$")
    if anchored:
        path = path[:-1]
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in path)
    return re.compile(rx + ("$" if anchored else ""))


class Robots:
    def __init__(self, text="", allow_all=False, disallow_all=False):
        self.allow_all = allow_all
        self.disallow_all = disallow_all
        self.groups = []  # [(агенты в нижнем регистре, [(allow, длина, regex)])]
        if text:
            self._parse(text)

    def _parse(self, text):
        agents, rules, in_rules = [], [], False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if in_rules:
                    self.groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if value:  # пустой Disallow = «разрешено всё», правила не добавляет
                    rules.append((key == "allow", len(value), _pattern(value)))
        if agents:
            self.groups.append((agents, rules))

    def _rules_for(self, product):
        product = product.lower()
        specific, star, named = [], [], False
        for agents, rules in self.groups:
            if any(a != "*" and a in product for a in agents):
                named = True
                specific += rules
            if "*" in agents:
                star += rules
        return specific if named else star

    def can_fetch(self, product, url):
        if self.disallow_all:
            return False
        if self.allow_all:
            return True
        p = urlparse(url)
        path = unquote(p.path or "/") + ("?" + p.query if p.query else "")
        best = None  # (длина, allow)
        for allow, length, rx in self._rules_for(product):
            if rx.match(path) and (best is None or length > best[0] or (length == best[0] and allow)):
                best = (length, allow)
        return True if best is None else best[1]
