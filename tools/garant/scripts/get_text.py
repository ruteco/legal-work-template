#!/usr/bin/env python3
"""Полный текст документа ГАРАНТ постранично, в виде HTML-фрагментов.

Без --out печатает JSON [{"number", "text"}, ...] в stdout.
С --out склеивает фрагменты и сохраняет как один HTML-файл.

Пример:
    python get_text.py 102004 --out ./102004.html
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import get_client, print_json, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", type=int, help="topic id документа")
    parser.add_argument("--out", type=Path, default=None, help="путь для сохранения склеенного HTML")
    args = parser.parse_args()

    def action() -> None:
        items = get_client().get_html(args.topic)
        if args.out:
            html = "\n".join(item.get("text", "") for item in items)
            args.out.write_text(html, encoding="utf-8")
            print(f"Сохранено: {args.out} ({len(items)} фрагментов)")
        else:
            print_json(items)

    run(action)


if __name__ == "__main__":
    main()
