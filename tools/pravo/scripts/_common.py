"""Общие утилиты для CLI-скриптов tools/pravo/scripts."""

from __future__ import annotations

import json
import sys

from pravo_client import PravoAPIError, PravoClient


def get_client() -> PravoClient:
    return PravoClient()


def print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def run(func) -> None:
    """Вызвать func() и аккуратно обработать ошибки API/аргументов."""
    try:
        func()
    except PravoAPIError as exc:
        print(f"Ошибка API pravo.gov.ru ({exc.status_code}): {exc.payload}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
