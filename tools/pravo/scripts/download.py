#!/usr/bin/env python3
"""Скачать PDF документа по номеру электронного опубликования (eoNumber).

Сохраняет файл локально (по умолчанию в текущую директорию) — в docs/refs
переносить вручную, только после подтверждения пользователем (см. AGENTS.md).

Пример:
    python download.py 2600202104190001 --out ./2600202104190001.pdf
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import get_client, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("eo_number", help="номер электронного опубликования")
    parser.add_argument("--out", type=Path, default=None, help="путь для сохранения (по умолчанию ./<eoNumber>.pdf)")
    args = parser.parse_args()

    def action() -> None:
        content = get_client().download_pdf(args.eo_number)
        out_path = args.out or Path(f"{args.eo_number}.pdf")
        out_path.write_bytes(content)
        print(f"Сохранено: {out_path} ({len(content)} байт)")

    run(action)


if __name__ == "__main__":
    main()
