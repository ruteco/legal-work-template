# -*- coding: utf-8 -*-
"""Проверка идентификаторов до обращения к DaData: опечатка в ИНН не должна тратить запрос из суточного лимита."""

import re

from .errors import UsageError

_INN10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def _control(digits, coeffs):
    return sum(int(d) * c for d, c in zip(digits, coeffs)) % 11 % 10


def inn_ok(s):
    if not re.fullmatch(r"\d{10}|\d{12}", s):
        return False
    if len(s) == 10:
        return _control(s, _INN10) == int(s[9])
    return _control(s, _INN12_1) == int(s[10]) and _control(s, _INN12_2) == int(s[11])


def ogrn_ok(s):
    if len(s) == 13 and s.isdigit():
        return int(s[:12]) % 11 % 10 == int(s[12])
    if len(s) == 15 and s.isdigit():
        return int(s[:14]) % 13 % 10 == int(s[14])
    return False


def parse_company_query(q, skip_check=False):
    """ИНН, ОГРН (ОГРНИП) или ИНН/КПП -> {"kind", "query", "kpp"}. Опечатки отсекаются контрольной суммой."""
    raw = re.sub(r"[\s ]", "", q or "")
    kpp = None
    if "/" in raw:
        raw, kpp = raw.split("/", 1)
        if not re.fullmatch(r"\d{9}", kpp):
            raise UsageError("КПП должен состоять из 9 цифр: %r." % kpp)
    if not raw.isdigit():
        raise UsageError("Ожидается ИНН, ОГРН или ИНН/КПП (только цифры): %r." % q,
                         hint="Для поиска по названию есть команда find.")
    if len(raw) in (10, 12):
        kind, ok = "inn", inn_ok(raw)
    elif len(raw) in (13, 15):
        kind, ok = "ogrn", ogrn_ok(raw)
    else:
        raise UsageError("Длина %d не похожа на ИНН (10 или 12 цифр) или ОГРН (13 или 15)." % len(raw),
                         hint="Для поиска по названию есть команда find.")
    if not ok and not skip_check:
        raise UsageError("Контрольная цифра %s не сходится: вероятна опечатка." % ("ИНН" if kind == "inn" else "ОГРН"),
                         hint="Проверьте номер; если уверены в нём, добавьте --skip-check.")
    if kpp and kind == "ogrn":
        raise UsageError("КПП указывается только вместе с ИНН (ИНН/КПП).")
    return {"kind": kind, "query": raw, "kpp": kpp}
