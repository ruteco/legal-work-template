# -*- coding: utf-8 -*-
"""Вход, хранение сессии и проверка её состояния.

Логин и пароль в этот модуль приходят только от человека (CLI спрашивает их через getpass) и
нигде не сохраняются: на диск попадают куки сессии и служебный rnd, больше ничего.
"""

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlparse

import requests

from . import paths
from .errors import AuthRequired, ConsultantError, ServiceError, SlotBusy
from .http import Http

LOGIN_HOST = "https://login.consultant.ru"
LOGIN_URL = LOGIN_HOST + "/?authType=login"
CGI = "https://cloud.consultant.ru/cloud/cgi/online.cgi"
JSON_ACCEPT = "application/json, text/plain, */*"
POLL_TIMEOUT_S = 120
POLL_STEP_S = 1.0


# --------------------------------------------------------------------------
# Хранилище сессии
# --------------------------------------------------------------------------


class SessionStore:
    def __init__(self, directory=None):
        self.dir = Path(directory) if directory else paths.secrets_dir()
        self.path = self.dir / "consultant-session.json"
        self.lock_path = self.dir / "consultant.lock"

    def exists(self):
        return self.path.exists()

    def load(self):
        if not self.exists():
            raise AuthRequired("Сохранённой сессии нет.")
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            raise AuthRequired("Файл сессии повреждён.")

    def save(self, state):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    def forget(self):
        if self.path.exists():
            self.path.unlink()

    @contextmanager
    def lock(self, timeout=180.0):
        """Одна команда клиента за раз: параллельные вызовы выстраиваются в очередь, а не дерутся за слот."""
        deadline = time.time() + timeout
        while True:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, ("%d %d" % (os.getpid(), time.time())).encode())
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - self.lock_path.stat().st_mtime > 600:  # зависший замок
                        self.lock_path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise SlotBusy("Другая команда клиента КонсультантПлюс ещё выполняется.", code="client_busy",
                                   hint="Подождите её завершения и повторите.")
                time.sleep(0.5)
        try:
            yield
        finally:
            try:
                self.lock_path.unlink()
            except OSError:
                pass


def session_from_state(state):
    s = requests.Session()
    for c in state.get("cookies", []):
        s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
    return s


def cookies_of(session):
    return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
             "expires": c.expires, "secure": c.secure} for c in session.cookies]


# --------------------------------------------------------------------------
# Конфиг сессии из HTML страницы
# --------------------------------------------------------------------------


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_session_cfg(html):
    i = html.find("refreshMaxCount")
    if i < 0:
        return None
    seg = html[max(0, i - 420): i + 320].replace('\\"', '"').replace("\\", "")

    def num(key):
        m = re.search(r'"%s"\s*:\s*(\d+)' % key, seg)
        return int(m.group(1)) if m else None

    cfg = {k: num(k) for k in ("create", "renew", "expire", "timeout", "refreshMaxCount")}
    m = re.search(r'"active"\s*:\s*(true|false)', seg)
    cfg["active"] = (m.group(1) == "true") if m else None
    return cfg


def check_session(http, state):
    """Состояние сессии без занятия слота: жива ли, когда истекает."""
    r = http.request("GET", CGI, params={"req": "home", "rnd": state.get("rnd")})
    alive = r.status_code == 200 and "Карточка поиска" in r.text
    out = {"valid": alive}
    if alive:
        cfg = parse_session_cfg(r.text) or {}
        out.update({"created": iso(cfg["create"]) if cfg.get("create") else None,
                    "expires": iso(cfg["expire"]) if cfg.get("expire") else None,
                    "idle_timeout_min": cfg.get("timeout"),
                    "slot_active": cfg.get("active")})
        if cfg.get("expire"):
            out["expires_in_min"] = round((cfg["expire"] - time.time() * 1000) / 60000, 1)
    return out


# --------------------------------------------------------------------------
# Вход
# --------------------------------------------------------------------------


class LoginFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form, self.fields, self._in = None, [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and "form_login" in (a.get("class") or ""):
            self.form, self._in = a, True
        elif self._in and tag in ("input", "select", "textarea"):
            self.fields.append({"name": a.get("name"), "type": a.get("type") or tag, "value": a.get("value")})

    def handle_endtag(self, tag):
        if tag == "form" and self._in:
            self._in = False


def _safe_url(u):
    """URL без значений параметров (токены сессии не попадают в вывод)."""
    p = urlparse(u)
    keep = {"req", "op", "base", "n", "content", "mode", "authType"}
    q = ["%s=%s" % (k, v if k in keep else "<%d>" % len(v)) for k, v in parse_qsl(p.query, keep_blank_values=True)]
    return "%s://%s%s%s" % (p.scheme, p.netloc, p.path, ("?" + "&".join(q)) if q else "")


def _payload(fields, login, password, remember):
    """Поля формы входа. confirmTOU (принятие условий) НЕ отправляется никогда: состояние учётной
    записи клиент не меняет."""
    out = []
    for f in fields:
        name, typ, val = f["name"], f["type"], f["value"]
        if not name or typ in ("submit", "button", "image") or name == "confirmTOU":
            continue
        if name == "LoginForm[login]":
            out.append((name, login))
        elif name == "LoginForm[password]":
            out.append((name, password))
        elif name == "LoginForm[rememberMe]":
            if typ == "hidden":
                out.append((name, val if val is not None else "0"))
            elif typ == "checkbox" and remember:
                out.append((name, val or "1"))
        else:
            out.append((name, val or ""))
    return out


def login(login_name, password, remember=False, http=None):
    """Полный вход. Возвращает состояние сессии (его сохраняет вызывающий)."""
    http = http or Http(min_interval=1.0)
    r = http.request("GET", LOGIN_URL)
    parser = LoginFormParser()
    parser.feed(r.text)
    if not parser.form:
        raise ServiceError("Форма входа не найдена: вёрстка страницы изменилась.", code="login_form_missing")

    r = http.request("GET", urljoin(LOGIN_URL, "/check-agreement/"), params={"login": login_name},
                     headers={"Accept": JSON_ACCEPT, "Referer": LOGIN_URL})
    try:
        need_tou = bool(r.json().get("agreementRequired"))
    except ValueError:
        need_tou = False
    if need_tou:
        raise ConsultantError(
            "Сервер просит принять условия использования для этого логина.", code="agreement_required",
            hint="Так сервер отвечает и на нераспознанный логин: сначала проверьте, что логин введён целиком "
                 "и точно (раскладка, регистр, часть после #). Если логин верный, условия принимает сам "
                 "пользователь в браузере; клиент состояние учётной записи не меняет и условия не принимает.")

    url = urljoin(LOGIN_URL, parser.form.get("data-suspend-login-action") or "/login/") + "?authType=login"
    r = http.request("POST", url, files=[(k, (None, v)) for k, v in
                                         _payload(parser.fields, login_name, password, remember)],
                     headers={"Accept": JSON_ACCEPT, "Origin": LOGIN_HOST, "Referer": LOGIN_URL},
                     allow_redirects=False)
    try:
        data = r.json()
    except ValueError:
        raise ServiceError("Сервер входа вернул не JSON (HTTP %s)." % r.status_code, code="login_bad_response")

    deadline = time.time() + POLL_TIMEOUT_S
    while data.get("authStatus") == "started" and time.time() < deadline:
        time.sleep(POLL_STEP_S)
        pr = http.request("GET", urljoin(LOGIN_URL, "/auth/"), params={"pid": data.get("pid")},
                          headers={"Accept": JSON_ACCEPT, "Referer": LOGIN_URL})
        try:
            data = pr.json()
        except ValueError:
            raise ServiceError("Опрос статуса входа вернул не JSON.", code="login_bad_response")

    status = data.get("authStatus")
    if status not in ("successful", "redirect") or not data.get("returnUrl"):
        raise ConsultantError("Вход не выполнен (authStatus=%s)." % status, code="login_failed",
                              hint="Проверьте логин и пароль и повторите auth login.",
                              data={"authStatus": status})

    r = http.request("GET", data["returnUrl"])
    rnd = dict(parse_qsl(urlparse(r.url).query)).get("rnd")
    if not rnd:
        m = re.search(r"[?&;]rnd=([A-Za-z0-9]+)", r.text)
        rnd = m.group(1) if m else None
    if not rnd or "Карточка поиска" not in r.text:
        title = re.search(r"<title>(.*?)</title>", r.text, re.S)
        visible = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", r.text,
                                              flags=re.S)).strip()
        raise ServiceError(
            "После входа не получена рабочая страница КонсультантПлюс.", code="login_no_session",
            data={"http_status": r.status_code, "final_url": _safe_url(r.url), "rnd_found": bool(rnd),
                  "redirects": [(h.status_code, _safe_url(h.url)) for h in r.history],
                  "title": title.group(1).strip()[:100] if title else None,
                  "page_text": re.sub(r"[A-Za-z0-9_-]{28,}", "<tok>", visible)[:300]})
    return {"saved_at": datetime.now(timezone.utc).isoformat(), "rnd": rnd,
            "session": parse_session_cfg(r.text), "cookies": cookies_of(http.session)}
