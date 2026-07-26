#!/usr/bin/env python3
"""Список Видов документов, опционально по блоку/категории/органу.

Пример:
    python document_types.py --authority-id 8005d8c9-4b6d-48d3-861a-2a37e69fccb3
"""

from __future__ import annotations

import argparse

from _common import get_client, print_json, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block", default=None, help="код Блока публикации")
    parser.add_argument("--category", default=None, help="код Категории принявшего органа")
    parser.add_argument("--authority-id", default=None, help="GUID принявшего органа (см. authorities.py)")
    args = parser.parse_args()

    def action() -> None:
        print_json(get_client().get_document_types(
            block=args.block, category=args.category, signatory_authority_id=args.authority_id,
        ))

    run(action)


if __name__ == "__main__":
    main()
