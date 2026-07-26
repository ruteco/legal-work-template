#!/usr/bin/env python3
"""Статистика опубликования документов по Блокам публикации за период.

Пример:
    python stats.py --period weekly
"""

from __future__ import annotations

import argparse

from _common import get_client, print_json, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", choices=["daily", "weekly", "monthly"], default="daily")
    args = parser.parse_args()

    def action() -> None:
        print_json(get_client().get_block_statistics(args.period))

    run(action)


if __name__ == "__main__":
    main()
