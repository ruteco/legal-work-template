# -*- coding: utf-8 -*-
"""Хранилище: ключ (secrets/), профиль рабочего пространства, счётчик запросов и кеш ответов (.cache/dadata/).

Ключ читается из переменной DADATA_API_KEY или из secrets/dadata.json и нигде больше не появляется:
ни в выводе команд, ни в кеше, ни в ошибках.
"""

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone

from . import paths
from .errors import AuthError, UsageError

KEY_FILE = "dadata.json"
KEY_FORMAT = re.compile(r"^[0-9a-fA-F]{40}$")      # ключи DaData — 40 шестнадцатеричных символов
DAILY_FREE_LIMIT = 10000


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ----- ключ ---------------------------------------------------------------------------------------------------

def key_path():
    return paths.secrets_dir() / KEY_FILE


def load_key():
    """(источник, ключ) либо (None, None)."""
    env = (os.environ.get("DADATA_API_KEY") or "").strip()
    if env:
        return "env", env
    data = _read_json(key_path(), {})
    if data.get("api_key"):
        return "file", data["api_key"]
    return None, None


def require_key():
    source, key = load_key()
    if not key:
        raise AuthError("Ключ DaData не задан.",
                        hint="Пользователь даёт ключ (личный кабинет dadata.ru), его сохраняет команда: auth set.")
    return key


def save_key(key):
    key = (key or "").strip()
    if not key:
        raise UsageError("Пустой ключ.")
    _write_json(key_path(), {"api_key": key, "saved_at": now_iso()})
    return key_path()


def forget_key():
    p = key_path()
    existed = p.exists()
    if existed:
        p.unlink()
    return existed


def key_info():
    source, key = load_key()
    data = _read_json(key_path(), {}) if source == "file" else {}
    return {"key_set": bool(key), "source": source, "saved_at": data.get("saved_at"),
            "format_ok": bool(key and KEY_FORMAT.match(key))}


# ----- профиль рабочего пространства ----------------------------------------------------------------------------

def _profile_path():
    return paths.cache_dir() / "profile.json"


def load_profile():
    p = _read_json(_profile_path(), {})
    p.setdefault("tier", None)
    p.setdefault("onboarding_done", False)
    p.setdefault("features", [])
    return p


def save_profile(profile):
    _write_json(_profile_path(), profile)


# ----- счётчик запросов за сутки (локальный, для предупреждения о лимите) ------------------------------------------

def _usage_path():
    return paths.cache_dir() / "usage.json"


def requests_today():
    u = _read_json(_usage_path(), {})
    return u.get("count", 0) if u.get("date") == date.today().isoformat() else 0


def count_request():
    today = date.today().isoformat()
    u = _read_json(_usage_path(), {})
    n = (u.get("count", 0) if u.get("date") == today else 0) + 1
    _write_json(_usage_path(), {"date": today, "count": n})
    return n


# ----- кеш ответов (локальный; хранить и распространять данные DaData вне рабочей области нельзя) ---------------------

def _cache_file(method, body):
    h = hashlib.sha1(json.dumps([method, body], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    d = paths.cache_dir() / "responses"
    d.mkdir(exist_ok=True)
    return d / (h + ".json")


def cache_get(method, body, ttl_hours):
    if ttl_hours <= 0:
        return None
    rec = _read_json(_cache_file(method, body), None)
    if not rec:
        return None
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(rec["fetched_at"])
    except (KeyError, ValueError):
        return None
    return rec if age.total_seconds() < ttl_hours * 3600 else None


def cache_clear():
    d = paths.cache_dir() / "responses"
    n = 0
    if d.exists():
        for f in d.glob("*.json"):
            f.unlink()
            n += 1
    return n


def cache_put(method, body, response):
    rec = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "method": method, "body": body,
           "response": response}
    _write_json(_cache_file(method, body), rec)
    return rec
