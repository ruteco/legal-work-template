# -*- coding: utf-8 -*-
"""Простой файловый кеш JSON с временем жизни (справочники, описание карточек, сохранённые поиски)."""

import hashlib
import json
import time
from pathlib import Path


class Cache:
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, kind, key):
        safe = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        d = self.root / kind
        d.mkdir(parents=True, exist_ok=True)
        return d / (safe + ".json")

    def get(self, kind, key, ttl):
        p = self._path(kind, key)
        if not p.exists():
            return None
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        if ttl is not None and time.time() - blob.get("t", 0) > ttl:
            return None
        return blob.get("data")

    def set(self, kind, key, data):
        self._path(kind, key).write_text(json.dumps({"t": time.time(), "data": data}, ensure_ascii=False),
                                         encoding="utf-8")

    # сохранённые поиски лежат под читаемым id
    def put_named(self, kind, name, data):
        d = self.root / kind
        d.mkdir(parents=True, exist_ok=True)
        (d / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def get_named(self, kind, name):
        p = self.root / kind / (name + ".json")
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
