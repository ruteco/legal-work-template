#!/usr/bin/env python3
"""Расширенные метаданные документа по номеру электронного опубликования (eoNumber).

eoNumber берётся из ответа search.py (поле "eoNumber").

Пример:
    python document_info.py 2600202104190001
"""

from __future__ import annotations

import argparse

from _common import get_client, print_json, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("eo_number", help="номер электронного опубликования")
    args = parser.parse_args()

    def action() -> None:
        print_json(get_client().get_document(args.eo_number))

    run(action)


if __name__ == "__main__":
    main()
