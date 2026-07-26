#!/usr/bin/env python3
"""Список Принявших органов (ПО), опционально по блоку/категории.

Пример:
    python authorities.py --block government
"""

from __future__ import annotations

import argparse

from _common import get_client, print_json, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block", default=None, help="код Блока публикации")
    parser.add_argument("--category", default=None, help="код Категории принявшего органа")
    args = parser.parse_args()

    def action() -> None:
        print_json(get_client().get_signatory_authorities(block=args.block, category=args.category))

    run(action)


if __name__ == "__main__":
    main()
