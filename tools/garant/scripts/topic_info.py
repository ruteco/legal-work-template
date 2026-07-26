#!/usr/bin/env python3
"""Метаданные документа ГАРАНТ по его topic id (лимит 300 запросов/месяц).

Пример:
    python topic_info.py 102004
"""

from __future__ import annotations

import argparse

from _common import get_client, print_json, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", type=int, help="topic id документа")
    args = parser.parse_args()

    def action() -> None:
        print_json(get_client().get_topic_info(args.topic))

    run(action)


if __name__ == "__main__":
    main()
