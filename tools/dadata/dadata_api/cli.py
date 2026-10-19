# -*- coding: utf-8 -*-
"""Командная строка dadata.py.

Контракт: успех — JSON в stdout, код 0; ошибка — JSON {"error": {code, message, hint}} в stderr и код возврата
(2 неверные параметры, 3 нет ключа или ключ не принят, 5 лимит или доступ запрещён, 6 сеть, 7 не найдено).
Ключ нигде не печатается. Данные DaData сохраняются только в .cache/dadata/ (в репозиторий не попадают).
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import features as features_mod
from . import paths, store, views
from .checks import parse_company_query
from .errors import AuthError, DadataError, LimitError, NetworkError, NotFound, UsageError
from .http import Client
from .report import report_markdown, requisites_text

REFERENCE_INN = "7707083893"     # крупная компания с полной карточкой: по набору её полей определяется тариф
TIER_TEXT = {"base": "бесплатный или «Лёгкий» (базовые поля)", "extended": "«Расширенный»", "maximum": "«Максимальный»"}


# ----- общее ----------------------------------------------------------------------------------------------------

def _client(a, **kw):
    return Client(min_interval=a.min_interval, cache_ttl_hours=0 if a.refresh else a.cache_hours, **kw)


def _meta(client, tier=None):
    m = dict(client.last_meta)
    n = store.requests_today()
    m["requests_today"] = n
    if tier == "base" or tier is None:
        m["daily_limit_free"] = store.DAILY_FREE_LIMIT
        if n >= 0.9 * store.DAILY_FREE_LIMIT:
            m["warning"] = "Использовано %d из %d бесплатных запросов в сутки." % (n, store.DAILY_FREE_LIMIT)
    return m


def _detect_tier(client):
    data = client.post("findById/party", {"query": REFERENCE_INN, "count": 1}, use_cache=False)
    sug = data.get("suggestions") or []
    if not sug:
        raise NotFound("Эталонная компания для определения тарифа не найдена.")
    tier = views.detect_tier(sug[0].get("data") or {})
    profile = store.load_profile()
    if profile.get("tier") != tier:
        profile["tier"] = tier
        store.cache_clear()          # кеш содержит ответы прежнего тарифа
    profile["tier_checked_at"] = store.now_iso()
    store.save_profile(profile)
    return tier


def _tier(client):
    t = store.load_profile().get("tier")
    return t or _detect_tier(client)


def _items(data):
    return data.get("suggestions") or []


# ----- auth -----------------------------------------------------------------------------------------------------

def cmd_auth(a):
    if a.action == "set":
        if a.stdin:
            key = sys.stdin.readline().strip()
        else:
            key = input("Ключ DaData (ввод виден): ").strip()
        if not key:
            raise UsageError("Ключ не получен.")
        res = {"verified": False}
        if not store.KEY_FORMAT.match(key):
            res["format_warning"] = "Ключ DaData обычно состоит из 40 шестнадцатеричных символов; проверьте, тот ли это ключ."
        if not a.no_check:
            try:
                client = Client(key=key, min_interval=a.min_interval)
                res["tier"] = _detect_tier(client)
                res["tier_text"] = TIER_TEXT[res["tier"]]
                res["verified"] = True
            except AuthError:
                raise                                   # неверный ключ не сохраняем
            except (LimitError, NetworkError) as e:
                res["check_error"] = e.message
                res["check_hint"] = e.hint
        store.save_key(key)
        res.update(saved=True, file="secrets/" + store.KEY_FILE,
                   note="Ключ сохранён в secrets/ и больше нигде не показывается.")
        return res
    if a.action == "status":
        info = store.key_info()
        p = store.load_profile()
        info.update(tier=p.get("tier"), tier_text=TIER_TEXT.get(p.get("tier")), tier_checked_at=p.get("tier_checked_at"),
                    onboarding_done=p.get("onboarding_done"), requests_today=store.requests_today(),
                    daily_limit_free=store.DAILY_FREE_LIMIT)
        if not info["key_set"]:
            info["next"] = "Ключа нет: пользователь даёт его, команда auth set --stdin сохраняет."
        return info
    if a.action == "check":
        client = Client(min_interval=a.min_interval)
        tier = _detect_tier(client)
        return {"verified": True, "tier": tier, "tier_text": TIER_TEXT[tier], "requests_today": store.requests_today()}
    if a.action == "forget":
        return {"removed": store.forget_key(), "note": "Профиль и кеш в .cache/dadata/ не тронуты."}
    raise UsageError("Неизвестное действие auth.")


# ----- компании -------------------------------------------------------------------------------------------------

def _find_company(client, a, q):
    """findById/party; по умолчанию только головная организация (или конкретный КПП)."""
    body = {"query": q["query"], "count": a.count}
    if q["kpp"]:
        body["kpp"] = q["kpp"]
    elif not getattr(a, "all_branches", False):
        body["branch_type"] = "MAIN"
    if getattr(a, "type", None):
        body["type"] = a.type
    if getattr(a, "status", None):
        body["status"] = [s.strip().upper() for s in a.status.split(",") if s.strip()]
    items = _items(client.post("findById/party", body))
    if not items and "branch_type" in body:          # у ИП и части записей признака подразделения нет
        body.pop("branch_type")
        items = _items(client.post("findById/party", body))
    return items


def cmd_company(a):
    q = parse_company_query(a.query, a.skip_check)
    client = _client(a)
    tier = _tier(client)
    items = _find_company(client, a, q)
    if not items:
        raise NotFound("Компания по %s %s не найдена." % ("ИНН" if q["kind"] == "inn" else "ОГРН", q["query"]),
                       hint="Проверьте номер; найдите компанию по названию командой find.")
    res = {"query": q, "tier": tier, "found": len(items), "companies": [views.company_view(i, tier) for i in items],
           "meta": _meta(client, tier)}
    if a.raw:
        res["raw"] = items
    return res


def cmd_find(a):
    client = _client(a)
    body = {"query": a.name, "count": min(max(a.count, 1), 20)}
    if a.type:
        body["type"] = a.type
    if a.status:
        body["status"] = [s.strip().upper() for s in a.status.split(",") if s.strip()]
    items = _items(client.post("suggest/party", body))
    if not items:
        raise NotFound("По запросу %r компаний не найдено." % a.name, hint="Попробуйте другое написание или ИНН/ОГРН.")
    return {"found": len(items), "companies": [views.company_view(i) for i in items],
            "note": "Подсказки возвращают только базовые поля; полная карточка — команда company по ИНН. Поиск по названию "
                    "разовый, по просьбе пользователя; массовая автоматическая обработка запрещена офертой DaData.",
            "meta": _meta(client)}


def cmd_branches(a):
    q = parse_company_query(a.query, a.skip_check)
    if q["kpp"]:
        raise UsageError("Для списка филиалов укажите ИНН или ОГРН без КПП.")
    client = _client(a)
    items = _items(client.post("findById/party", {"query": q["query"], "count": 300}))
    if not items:
        raise NotFound("Компания %s не найдена." % q["query"])
    rows = [{"kpp": i["data"].get("kpp"), "branch_type": i["data"].get("branch_type"),
             "status": views.STATUS_RU.get((i["data"].get("state") or {}).get("status")),
             "address": (i["data"].get("address") or {}).get("value")} for i in items]
    return {"name": items[0].get("value"), "count": len(rows), "units": rows,
            "note": "У части филиалов КПП в данных ФНС нет (около четверти), такие подразделения этим методом не находятся.",
            "meta": _meta(client)}


def cmd_requisites(a):
    q = parse_company_query(a.query, a.skip_check)
    client = _client(a)
    items = _find_company(client, a, q)
    if not items:
        raise NotFound("Компания %s не найдена." % q["query"])
    v = views.company_view(items[0])
    return {"text": requisites_text(v), "warnings": v["warnings"], "status": v["status"]["text"], "meta": _meta(client),
            "note": "Расчётный счёт и банк в открытых данных ФНС отсутствуют: берутся у контрагента. Должность и ФИО "
                    "руководителя в именительном падеже: падеж для договора ставит юрист."}


def cmd_report(a):
    q = parse_company_query(a.query, a.skip_check)
    client = _client(a)
    tier = _tier(client)
    items = _find_company(client, a, q)
    if not items:
        raise NotFound("Компания %s не найдена." % q["query"])
    v = views.company_view(items[0], tier)
    meta = client.last_meta
    md = report_markdown(v, meta.get("fetched_at", store.now_iso()), meta.get("cached", False), tier)
    if a.out:
        path = Path(a.out)
    else:
        d = paths.cache_dir() / "reports"
        d.mkdir(exist_ok=True)
        path = d / ("%s%s_%s.md" % (v["inn"], "_" + v["kpp"] if v["kpp"] else "", date.today().isoformat()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    return {"path": str(path), "company": v["name"]["short"] or v["name"]["full"], "status": v["status"]["text"],
            "warnings": v["warnings"], "markdown": md, "meta": _meta(client, tier),
            "note": "Карточка лежит в .cache/dadata/ (данные DaData не публикуются и не копируются в шаблон). В материалы дела "
                    "переносить только по решению пользователя, с датой получения."}


# ----- справочники ----------------------------------------------------------------------------------------------

def cmd_bank(a):
    client = _client(a)
    if a.search:
        items = _items(client.post("suggest/bank", {"query": a.query, "count": min(max(a.count, 1), 20)}))
    else:
        body = {"query": a.query}
        if a.kpp:
            body["kpp"] = a.kpp
        items = _items(client.post("findById/bank", body))
    if not items:
        raise NotFound("Банк %r не найден." % a.query,
                       hint="Точный поиск идёт по БИК, SWIFT, ИНН или рег. номеру; по названию — флаг --search.")
    return {"found": len(items), "banks": [views.bank_view(i) for i in items], "meta": _meta(client)}


def cmd_court(a):
    client = _client(a)
    if a.code:
        items = _items(client.post("findById/court", {"query": a.query}))
    else:
        body = {"query": a.query}
        if a.type:
            body["filters"] = [{"court_type": a.type.upper()}]
        items = _items(client.post("suggest/court", body))
    if not items:
        raise NotFound("Суд по запросу %r не найден." % a.query)
    return {"found": len(items), "courts": [views.court_view(i) for i in items[: a.limit]], "meta": _meta(client),
            "note": "Справочник из ГАС «Правосудие» и сайтов судов: подсудность определяет закон, а не справочник."}


def cmd_tax_office(a):
    client = _client(a)
    if a.code:
        items = _items(client.post("findById/fns_unit", {"query": a.query}))
    else:
        body = {"query": a.query}
        if a.region:
            body["filters"] = [{"region_code": a.region}]
        items = _items(client.post("suggest/fns_unit", body))
    if not items:
        raise NotFound("Налоговая инспекция по запросу %r не найдена." % a.query)
    return {"found": len(items), "offices": [views.tax_office_view(i) for i in items[: a.limit]], "meta": _meta(client)}


# ----- профиль и каталог ----------------------------------------------------------------------------------------

def cmd_features(a):
    p = store.load_profile()
    return {"tier": p.get("tier"), "tier_text": TIER_TEXT.get(p.get("tier")), "features": features_mod.FEATURES,
            "not_covered_by_dadata": features_mod.NOT_COVERED,
            "selected_by_user": p.get("features"),
            "note": "Покупку тарифа, пополнение счёта и регистрацию делает только пользователь."}


def cmd_profile(a):
    p = store.load_profile()
    if a.action == "show":
        return p
    if a.action == "set":
        if a.features is not None:
            p["features"] = [f.strip() for f in a.features.split(",") if f.strip()]
        if a.note is not None:
            p["note"] = a.note
        if a.onboarded:
            p["onboarding_done"] = True
            p["onboarded_at"] = store.now_iso()
        store.save_profile(p)
        return p
    if a.action == "reset":
        p.update(onboarding_done=False, features=[])
        p.pop("note", None)
        p.pop("onboarded_at", None)
        store.save_profile(p)
        return p
    raise UsageError("Неизвестное действие profile.")


# ----- парсер ---------------------------------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(prog="dadata.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--min-interval", type=float, default=0.25, help="пауза между запросами, с")
    ap.add_argument("--cache-hours", type=float, default=24.0, help="срок жизни локального кеша ответов, ч (0 — не использовать)")
    ap.add_argument("--refresh", action="store_true", help="не брать ответ из кеша")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("auth", help="ключ DaData: set (сохранить в secrets/), status, check (проверить и определить тариф), forget")
    p.add_argument("action", choices=["set", "status", "check", "forget"])
    p.add_argument("--stdin", action="store_true", help="set: взять ключ из стандартного ввода (иначе — запрос в терминале, ввод виден)")
    p.add_argument("--no-check", action="store_true", help="set: не проверять ключ запросом")
    p.set_defaults(fn=cmd_auth)

    def company_args(p):
        p.add_argument("--query", required=True, help="ИНН, ОГРН (ОГРНИП) или ИНН/КПП")
        p.add_argument("--skip-check", action="store_true", help="не проверять контрольную цифру ИНН/ОГРН")
        p.add_argument("--all-branches", action="store_true", help="не ограничиваться головной организацией")
        p.add_argument("--type", choices=["LEGAL", "INDIVIDUAL"])
        p.add_argument("--status", help="через запятую: ACTIVE, LIQUIDATING, LIQUIDATED, BANKRUPT, REORGANIZING")
        p.add_argument("--count", type=int, default=10)

    p = sub.add_parser("company", help="реквизиты и состояние компании или ИП по ИНН/ОГРН")
    company_args(p)
    p.add_argument("--raw", action="store_true", help="добавить необработанный ответ DaData")
    p.set_defaults(fn=cmd_company)

    p = sub.add_parser("find", help="поиск компании по названию (разово, по просьбе пользователя)")
    p.add_argument("--name", required=True)
    p.add_argument("--type", choices=["LEGAL", "INDIVIDUAL"])
    p.add_argument("--status")
    p.add_argument("--count", type=int, default=5, help="до 20")
    p.set_defaults(fn=cmd_find)

    p = sub.add_parser("branches", help="филиалы компании")
    p.add_argument("--query", required=True)
    p.add_argument("--skip-check", action="store_true")
    p.set_defaults(fn=cmd_branches)

    p = sub.add_parser("requisites", help="блок реквизитов стороны для договора")
    company_args(p)
    p.set_defaults(fn=cmd_requisites)

    p = sub.add_parser("report", help="карточка контрагента в markdown (файл в .cache/dadata/reports/ или --out)")
    company_args(p)
    p.add_argument("--out")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("bank", help="банк по БИК, SWIFT, ИНН, рег. номеру (или поиск по названию с --search)")
    p.add_argument("--query", required=True)
    p.add_argument("--kpp")
    p.add_argument("--search", action="store_true", help="подсказки по названию или части БИК вместо точного поиска")
    p.add_argument("--count", type=int, default=5)
    p.set_defaults(fn=cmd_bank)

    p = sub.add_parser("court", help="справочник судов")
    p.add_argument("--query", required=True, help="название, адрес или код суда")
    p.add_argument("--code", action="store_true", help="--query — точный код суда")
    p.add_argument("--type", help="тип суда: MS, RS, OS, VS, AS, AA, AO, AI, ... (см. README)")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(fn=cmd_court)

    p = sub.add_parser("tax-office", help="справочник налоговых инспекций")
    p.add_argument("--query", required=True, help="код, название или адрес инспекции")
    p.add_argument("--code", action="store_true", help="--query — точный код инспекции или ИНН")
    p.add_argument("--region", help="первые две цифры кода региона, например 77")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(fn=cmd_tax_office)

    sub.add_parser("features", help="каталог возможностей DaData, тарифы, что реализовано").set_defaults(fn=cmd_features)

    p = sub.add_parser("profile", help="профиль рабочего пространства: тариф, выбор функций после первого использования")
    p.add_argument("action", choices=["show", "set", "reset"])
    p.add_argument("--features", help="set: id функций через запятую (см. features)")
    p.add_argument("--note")
    p.add_argument("--onboarded", action="store_true", help="set: отметить, что пользователя уже спросили о функциях")
    p.set_defaults(fn=cmd_profile)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    a = build_parser().parse_args(argv)
    indent = 2 if a.pretty else None
    try:
        print(json.dumps(a.fn(a), ensure_ascii=False, indent=indent))
        return 0
    except DadataError as e:
        print(json.dumps(e.to_dict(), ensure_ascii=False, indent=indent), file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(json.dumps({"error": {"code": "internal_error", "message": "%s: %s" % (type(e).__name__, e)}},
                         ensure_ascii=False, indent=indent), file=sys.stderr)
        return 1
