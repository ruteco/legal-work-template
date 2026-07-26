#!/usr/bin/env python3
"""Поиск документов (НПА) на publication.pravo.gov.ru.

Примеры:
    python search.py --text "контрактной системе" --page-size 10
    python search.py --number 44-ФЗ --number-search-type 3
    python search.py --block government --date-from 2024-01-01 --date-to 2024-01-31
"""

from __future__ import annotations

import argparse

from _common import get_client, print_json, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", dest="DocumentText", help="поиск по тексту документа")
    parser.add_argument("--name", dest="Name", help="поиск по названию документа")
    parser.add_argument("--complex-name", dest="ComplexName", help="поиск по составному названию")
    parser.add_argument("--number", dest="Number", help="номер документа")
    parser.add_argument("--number-search-type", type=int, default=None, dest="NumberSearchType",
                         help="0 точно, 1 начинается с, 2 заканчивается на, 3 содержит")
    parser.add_argument("--block", dest="Block", help="код Блока публикации (см. blocks.py)")
    parser.add_argument("--category", dest="Category", help="код Категории принявшего органа (см. categories.py)")
    parser.add_argument("--authority-id", dest="SignatoryAuthorityId", help="GUID принявшего органа (см. authorities.py)")
    parser.add_argument("--document-type-id", dest="DocumentTypeId", help="GUID вида документа (см. document_types.py)")
    parser.add_argument("--date-from", dest="DocumentDateFrom", help="дата подписания, начало периода (YYYY-MM-DD)")
    parser.add_argument("--date-to", dest="DocumentDateTo", help="дата подписания, конец периода (YYYY-MM-DD)")
    parser.add_argument("--publish-date-from", dest="PublishDateFrom", help="дата публикации, начало периода")
    parser.add_argument("--publish-date-to", dest="PublishDateTo", help="дата публикации, конец периода")
    parser.add_argument("--period-type", dest="PeriodType", choices=["daily", "weekly", "monthly", "day"],
                         help="фиксированный период публикации")
    parser.add_argument("--date", dest="Date", help="дата публикации при --period-type day (YYYY-MM-DD)")
    parser.add_argument("--page-size", type=int, default=30, dest="PageSize", help="10 | 30 | 100 | 200")
    parser.add_argument("--index", type=int, default=1, dest="Index", help="номер страницы результатов")
    parser.add_argument("--sorted-by", type=int, default=None, dest="SortedBy",
                         help="0 дата подписания, 1 вид, 2 орган, 3 номер, 4 дата публикации, 5 номер публикации")
    args = parser.parse_args()

    def action() -> None:
        params = {k: v for k, v in vars(args).items() if v is not None}
        result = get_client().search(**params)
        print_json(result)

    run(action)


if __name__ == "__main__":
    main()
