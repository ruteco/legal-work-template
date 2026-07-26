#!/usr/bin/env python3
"""Список Блоков публикации (и подблоков, если задан --parent).

Пример:
    python blocks.py
    python blocks.py --parent assembly
"""

from __future__ import annotations

import argparse

from _common import get_client, print_json, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", default=None, help="код родительского блока, чтобы получить подблоки")
    args = parser.parse_args()

    def action() -> None:
        print_json(get_client().get_public_blocks(parent=args.parent))

    run(action)


if __name__ == "__main__":
    main()
