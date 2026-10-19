# -*- coding: utf-8 -*-
"""Где что лежит. Ключ — в secrets/, кеш и профиль — в .cache/; в шаблон не попадает ни то, ни другое."""

import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
TOOL = PKG.parent               # tools/dadata
ROOT = TOOL.parents[1]          # корень рабочей области


def _private_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    gi = path / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n!.gitignore\n", encoding="utf-8")
    return path


def secrets_dir():
    return _private_dir(Path(os.environ.get("DADATA_SECRETS_DIR") or ROOT / "secrets"))


def cache_dir():
    return _private_dir(Path(os.environ.get("DADATA_CACHE_DIR") or ROOT / ".cache" / "dadata"))
