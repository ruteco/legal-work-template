#!/usr/bin/env python3
"""Скачать документ ГАРАНТ целиком (лимит 30 документов/месяц).

Сохраняет файл локально (по умолчанию в текущую директорию) — в docs/refs
переносить вручную, только после подтверждения пользователем (см. AGENTS.md).

Пример:
    python download.py 102004 --fmt pdf --out ./102004.pdf
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import get_client, run

DOWNLOADERS = {
    "pdf": lambda client, topic: client.download_pdf(topic),
    "rtf": lambda client, topic: client.download_rtf(topic),
    "odt": lambda client, topic: client.download_odt(topic),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", type=int, help="topic id документа")
    parser.add_argument("--fmt", choices=sorted(DOWNLOADERS), default="pdf", help="формат файла")
    parser.add_argument("--out", type=Path, default=None, help="путь для сохранения (по умолчанию ./garant_<topic>.<fmt>)")
    args = parser.parse_args()

    def action() -> None:
        client = get_client()
        content = DOWNLOADERS[args.fmt](client, args.topic)
        out_path = args.out or Path(f"garant_{args.topic}.{args.fmt}")
        out_path.write_bytes(content)
        print(f"Сохранено: {out_path} ({len(content)} байт)")

    run(action)


if __name__ == "__main__":
    main()
