# -*- coding: utf-8 -*-
"""Где что лежит. Секреты и кеш — только в рабочей области, в шаблон они не попадают."""

import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
TOOL = PKG.parent               # tools/consultant-api
ROOT = TOOL.parents[1]          # корень рабочей области
SPLITTER = ROOT / ".skills" / "legal-doc-splitter" / "scripts" / "split_document.py"


def secrets_dir():
    d = Path(os.environ.get("CONSULTANT_SECRETS_DIR") or ROOT / "secrets")
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n!.gitignore\n", encoding="utf-8")
    return d


def cache_dir():
    d = Path(os.environ.get("CONSULTANT_CACHE_DIR") or ROOT / ".cache" / "consultant")
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n!.gitignore\n", encoding="utf-8")
    return d
