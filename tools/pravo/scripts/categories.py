#!/usr/bin/env python3
"""Список Категорий принявших органов по Блоку публикации.

Пример:
    python categories.py --block government
"""

from __future__ import annotations

import argparse

from _common import get_client, print_json, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block", required=True, help="код Блока публикации (см. blocks.py)")
    args = parser.parse_args()

    def action() -> None:
        print_json(get_client().get_categories(args.block))

    run(action)


if __name__ == "__main__":
    main()
