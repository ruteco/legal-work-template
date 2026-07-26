#!/usr/bin/env python3
"""Проверить, менялись ли документы после заданной даты (до 100 topic за вызов).

Полезно для мониторинга актуальности источников, уже сохранённых в docs/refs.md.

Пример:
    python find_modified.py 77682742 45069704 --since 2019-07-01 --events
"""

from __future__ import annotations

import argparse

from _common import get_client, print_json, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topics", type=int, nargs="+", help="topic id документов (до 100)")
    parser.add_argument("--since", required=True, dest="mod_date", help="дата в формате YYYY-MM-DD")
    parser.add_argument("--events", action="store_true", dest="need_events", help="вернуть историю событий по документу")
    args = parser.parse_args()

    def action() -> None:
        print_json(get_client().find_modified(args.topics, args.mod_date, need_events=args.need_events))

    run(action)


if __name__ == "__main__":
    main()
