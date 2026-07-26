#!/usr/bin/env python3
"""Поиск документов в системе ГАРАНТ.

Пример:
    python search.py "44-фз о контрактной системе" --kind 001 002
    python search.py "& BOOL(& MorphoText(налог))" --query --sort 1
"""

from __future__ import annotations

import argparse

from _common import get_client, print_json, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", help="поисковая фраза или запрос на языке запросов (см. --query)")
    parser.add_argument("--query", action="store_true", dest="is_query",
                         help="трактовать text как запрос на специальном языке (см. docs/api-spec-summary.md)")
    parser.add_argument("--count", type=int, default=10, help="макс. число документов в ответе (1-30)")
    parser.add_argument("--kind", nargs="*", default=None,
                         help="коды типов: 001 федеральное, 002 региональное, 003 судебная практика, 004 пользовательские")
    parser.add_argument("--sort", type=int, default=0,
                         help="0 релевантность, 1 дата документа, 2 дата изменения, 3 юридическая сила")
    parser.add_argument("--sort-order", type=int, default=0, dest="sort_order",
                         help="0 убывание, 1 возрастание")
    args = parser.parse_args()

    def action() -> None:
        client = get_client()
        documents = client.search(
            args.text,
            is_query=args.is_query,
            count=args.count,
            kind=args.kind,
            sort=args.sort,
            sort_order=args.sort_order,
        )
        print_json(documents)

    run(action)


if __name__ == "__main__":
    main()
