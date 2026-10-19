# -*- coding: utf-8 -*-
"""Где что лежит. Кеш и загрузки — в рабочей области, в репозиторий они не попадают."""

import os
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent   # tools/science-search
ROOT = TOOL.parents[1]                           # корень рабочей области


def cache_dir():
    d = Path(os.environ.get("SCIENCE_CACHE_DIR") or ROOT / ".cache" / "science")
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n!.gitignore\n", encoding="utf-8")
    return d
