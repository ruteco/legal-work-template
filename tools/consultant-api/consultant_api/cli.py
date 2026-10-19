# -*- coding: utf-8 -*-
"""Командная строка клиента КонсультантПлюс.

Контракт для агентов и skills: успешный результат — JSON в stdout, код 0; ошибка — JSON
{"error": {code, message, hint}} в stderr и ненулевой код (3 нужен вход, 4 слот занят,
5 ошибка сервиса, 6 сеть, 7 не найдено, 8 выгрузка не прошла проверку, 2 неверные параметры).
Клиент сам ничего не сохраняет в docs/refs: файлы попадают в .cache/consultant/ или в --out.
"""

import argparse
import json
import os
import re
import sys

from . import paths
from .auth import SessionStore, check_session, cookies_of, login, session_from_state
from .cache import Cache
from .client import Client
from .errors import AUTH_HINT, ConsultantError, UsageError
from .http import Http


def run_with_client(a, work, needs_slot=True):
    store = SessionStore()
    with store.lock():
        state = store.load()
        http = Http(session_from_state(state), min_interval=a.min_interval)
        client = Client(state, http, Cache(paths.cache_dir()))
        try:
            if needs_slot:
                with client.slot():
                    return work(client)
            return work(client)
        finally:
            state["cookies"] = cookies_of(http.session)  # куки продлеваются при каждом запросе
            store.save(state)


# --------------------------------------------------------------------------
# Команды
# --------------------------------------------------------------------------


def ask_visible(prompt, mask_after=False):
    """Ввод виден при наборе, чтобы пользователь мог сверить значение. mask_after: после Enter строка
    перерисовывается звёздочками, чтобы пароль не остался на экране терминала (его может читать ассистент)."""
    value = input(prompt)
    if mask_after and sys.stdout.isatty():
        sys.stdout.write("\x1b[1A\x1b[2K%s%s  (%d симв.)\n" % (prompt, "*" * len(value), len(value)))
        sys.stdout.flush()
    return value


def cmd_auth_login(a):
    if not sys.stdin.isatty():
        raise UsageError("auth login нужно запускать в интерактивном терминале: логин и пароль вводит пользователь.")
    os.system("")  # включает ANSI-последовательности в консоли Windows
    print("Ввод виден при наборе. После Enter пароль на экране заменяется звёздочками.")
    login_name = ask_visible("Логин:  ").strip()
    password = ask_visible("Пароль: ", mask_after=True)
    print("Логин: %d симв., пароль: %d симв. Выполняю вход…" % (len(login_name), len(password)))
    store = SessionStore()
    with store.lock():
        state = login(login_name, password, a.remember, Http(min_interval=1.0))
        store.save(state)
    cfg = state.get("session") or {}
    return {"ok": True, "message": "Вход выполнен, сессия сохранена в secrets/.",
            "idle_timeout_min": cfg.get("timeout")}


def cmd_auth_status(a):
    store = SessionStore()
    if not store.exists():
        return {"logged_in": False, "hint": AUTH_HINT}
    with store.lock():
        state = store.load()
        http = Http(session_from_state(state), min_interval=a.min_interval)
        res = check_session(http, state)
        state["cookies"] = cookies_of(http.session)
        store.save(state)
    out = {"logged_in": res["valid"]}
    out.update(res)
    if not res["valid"]:
        out["hint"] = AUTH_HINT
    return out


def cmd_auth_forget(a):
    SessionStore().forget()
    return {"ok": True, "message": "Локальная сессия удалена."}


def cmd_slot_release(a):
    def work(c):
        j = c.deactivate()
        return {"released": "session" in j, "slot_active": (j.get("session") or {}).get("active")}
    return run_with_client(a, work, needs_slot=False)


def cmd_fields(a):
    return run_with_client(a, lambda c: c.card_fields(a.div))


def cmd_dict(a):
    def work(c):
        f = c.field(a.div, a.field)
        return {"div": a.div, "field": f["name"], "items": c.dict_values(a.div, f["num"], a.filter or "", a.limit)}
    return run_with_client(a, work)


def cmd_dispute(a):
    return run_with_client(a, lambda c: c.dispute_find(a.court, a.filter, a.claims_for))


def cmd_quick(a):
    return run_with_client(a, lambda c: c.quick(a.text, a.limit))


def cmd_tree(a):
    def work(c):
        f = c.field(a.div, a.field)
        if not f["tree"]:
            raise UsageError("Поле «%s» не дерево; для него команда dict." % f["name"])
        return {"div": a.div, "field": f["name"], "matches": c.tree_find(a.div, f, a.filter)}
    return run_with_client(a, work)


STATUS_ALIASES = {"current": "2", "lost": "1", "pending": "3"}  # коды справочника «Поиск по статусу»
TEXT_MODES = {"auto": "b", "doc": "d", "paragraph": "p", "phrase": "w"}


def text_mode_from_args(a):
    """Код режима текстового поля: [!]<близость>[число][c][l]. Близость: w, p, d, t<N>, tp<N>; ! — точные формы; c — без синонимов; l — без расширения по ссылкам."""
    m = a.text_mode
    if m in TEXT_MODES:
        base = TEXT_MODES[m]
    elif re.fullmatch(r"near=\d+", m):
        base = "t" + m.split("=")[1]
    elif re.fullmatch(r"near-paragraph=\d+", m):
        base = "tp" + m.split("=")[1]
    elif re.fullmatch(r"!?(b|[wpd]|tp?\d*)[cl]*", m):
        return m  # готовый код режима
    else:
        raise UsageError("Неизвестный режим текста: %r." % m,
                         hint="auto, doc, paragraph, phrase, near=N, near-paragraph=N или готовый код (например !w).")
    if base == "b":
        if a.exact_forms or a.no_synonyms or a.no_links:
            raise UsageError("Флаги --exact-forms/--no-synonyms/--no-links неприменимы к режиму auto.",
                             hint="Выберите режим doc, paragraph, phrase или near=N.")
        return base
    return ("!" if a.exact_forms else "") + base + ("c" if a.no_synonyms else "") + ("l" if a.no_links else "")


def conditions_from_args(a):
    conds = []
    if a.text:
        conds.append({"field": "text", "text": a.text, "mode": text_mode_from_args(a)})
    if a.name:
        conds.append({"field": "name", "text": a.name, "mode": text_mode_from_args(a)})
    if a.number:
        conds.append({"field": "number", "names": a.number})
    if a.date:
        conds.append({"field": "date", "mode": "exact", "date": a.date})
    if a.date_after:
        conds.append({"field": "date", "mode": "later", "date": a.date_after})
    if a.date_before:
        conds.append({"field": "date", "mode": "before", "date": a.date_before})
    if a.date_from or a.date_to:
        if not (a.date_from and a.date_to):
            raise UsageError("Для диапазона дат нужны оба параметра: --date-from и --date-to.")
        conds.append({"field": "date", "mode": "range", "date": a.date_from, "date2": a.date_to})
    if a.organ:
        conds.append({"field": "organ", "names": a.organ})
    if a.doctype:
        conds.append({"field": "doctype", "names": a.doctype})
    if a.topic:
        conds.append({"field": "topic", "names": a.topic})
    if a.result:
        conds.append({"field": "result", "names": a.result})
    if a.judge:
        conds.append({"field": "judge", "names": a.judge})
    if a.status:
        if all(s in STATUS_ALIASES for s in a.status):
            conds.append({"field": "status", "raw": ",".join(STATUS_ALIASES[s] for s in a.status)})
        elif any(s in STATUS_ALIASES for s in a.status):
            raise UsageError("Не смешивайте псевдонимы статуса (current, lost, pending) и названия.")
        else:
            conds.append({"field": "status", "names": a.status})
    if a.claim and len(a.dispute or []) != 1:
        raise UsageError("--claim требует ровно одну --dispute (категория нужна как контекст поиска требований).")
    if a.dispute or a.claim:
        if a.div != "ARB":
            raise UsageError("Категории спора и требования есть только в разделе ARB (судебная практика).")
        if a.claim:
            conds.append({"field": "claim", "court": a.court, "category": a.dispute[0], "names": a.claim})
        else:
            conds.append({"field": "dispute", "court": a.court, "names": a.dispute})
    for item in a.raw or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise UsageError("--raw ожидает ПОЛЕ=ЗНАЧЕНИЕ, получено %r." % item)
        conds.append({"field": key, "raw": value})
    if not conds:
        raise UsageError("Не задано ни одного условия поиска.", hint="Например: --text \"утверждение устава\".")
    return conds


def cmd_search(a):
    conds = conditions_from_args(a)

    def work(c):
        rec = c.search(a.div, conds)
        if not rec.get("id"):
            return {"search_id": None, "div": a.div, "total": 0, "conditions": rec.get("conditions"), "tree": []}
        default = c.pick_node(rec)
        out = {"search_id": rec["id"], "div": a.div, "total": rec["total"], "conditions": rec["conditions"],
               "default_node": default["node"],
               "tree": [{"node": n["node"], "level": n["level"], "name": n["name"], "base": n["base"],
                         "count": n["count"]} for n in rec["nodes"] if n["count"]]}
        if a.list:
            out["list"] = c.list(rec["id"], None, 0, a.list)
        return out

    return run_with_client(a, work)


def cmd_list(a):
    return run_with_client(a, lambda c: c.list(a.search, a.node, a.offset, a.limit))


def cmd_edition(a):
    def work(c):
        if a.on_date:
            return {"on_date": a.on_date, "edition": c.edition_on_date(a.base, a.n, a.on_date)}
        if a.all:
            res = c.editions(a.base, a.n)
            counts = {}
            for e in res["editions"]:
                counts[e["status"]] = counts.get(e["status"], 0) + 1
            return {"current_n": res["current_n"], "count": res["count"], "by_status": counts,
                    "editions": res["editions"][: a.limit], "note": "показаны новейшие %d (по номеру)" % a.limit}
        return c.edition(a.base, a.n)
    return run_with_client(a, work)


def cmd_export(a):
    def work(c):
        exp = c.export(a.base, a.n, a.format, a.out, a.exact, a.on_date)
        entry_file = exp["path"]
        if a.split:
            exp["split"] = c.split(exp, a.split_dir)
            entry_file = exp["split"]["readme"]
        exp["refs_entry_suggestion"] = {
            "name": exp["title"] or "%s/%s" % (exp["base"], exp["n"]),
            "edition": exp["edition_date"], "source": "КонсультантПлюс (подписка пользователя)",
            "file": entry_file,
            "note": "Сохранять в docs/refs только после согласия пользователя (AGENTS.md)."}
        return exp
    return run_with_client(a, work)


# --------------------------------------------------------------------------
# Разбор аргументов
# --------------------------------------------------------------------------


def build_parser():
    ap = argparse.ArgumentParser(prog="consultant.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pretty", action="store_true", help="форматированный JSON")
    ap.add_argument("--min-interval", type=float, default=0.8, help="пауза между запросами, с")
    sub = ap.add_subparsers(dest="cmd", required=True)

    au = sub.add_parser("auth", help="вход и состояние сессии").add_subparsers(dest="sub", required=True)
    p = au.add_parser("login", help="интерактивный вход (логин и пароль вводит человек)")
    p.add_argument("--remember", action="store_true", help="включить «Запомнить меня»")
    p.set_defaults(fn=cmd_auth_login)
    au.add_parser("status", help="жива ли сессия (слот не занимает)").set_defaults(fn=cmd_auth_status)
    au.add_parser("forget", help="удалить локальную сессию").set_defaults(fn=cmd_auth_forget)

    sl = sub.add_parser("slot", help="слот подключения").add_subparsers(dest="sub", required=True)
    sl.add_parser("release", help="освободить слот этой сессии").set_defaults(fn=cmd_slot_release)

    p = sub.add_parser("fields", help="поля карточки раздела (LAW, ARB, AP, ...)")
    p.add_argument("--div", default="LAW")
    p.set_defaults(fn=cmd_fields)

    p = sub.add_parser("dict", help="значения справочника поля (принявший орган, вид документа, ...)")
    p.add_argument("--div", default="LAW")
    p.add_argument("--field", required=True, help="organ, doctype, number, ... или название/номер поля")
    p.add_argument("--filter", help="часть названия")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_dict)

    p = sub.add_parser("search", help="поиск: дерево результатов по базам и cacheid")
    p.add_argument("--div", default="LAW", help="раздел карточки: LAW (законодательство), ARB (судебная практика)")
    p.add_argument("--text", help="текст документа")
    p.add_argument("--text-mode", default="auto",
                   help="режим текста: auto (по умолчанию), doc (слова в документе, любой порядок), paragraph, "
                        "phrase, near=N, near-paragraph=N или готовый код (например !w)")
    p.add_argument("--exact-forms", action="store_true", help="слова строго в заданной форме (префикс !)")
    p.add_argument("--no-synonyms", action="store_true", help="без синонимов (флаг c)")
    p.add_argument("--no-links", action="store_true", help="без расширения по ссылкам (флаг l)")
    p.add_argument("--name", help="название документа")
    p.add_argument("--number", action="append", help="номер документа (можно несколько раз)")
    p.add_argument("--date", help="точная дата ДД.ММ.ГГГГ")
    p.add_argument("--date-after", help="позже чем ДД.ММ.ГГГГ")
    p.add_argument("--date-before", help="раньше чем ДД.ММ.ГГГГ")
    p.add_argument("--date-from", help="диапазон: с даты")
    p.add_argument("--date-to", help="диапазон: по дату")
    p.add_argument("--organ", action="append", help="принявший орган (можно несколько раз)")
    p.add_argument("--doctype", action="append", help="вид документа (можно несколько раз)")
    p.add_argument("--topic", action="append", help="тематика (рубрикатор, только LAW; можно несколько раз)")
    p.add_argument("--result", action="append", help="результат рассмотрения (только ARB; можно несколько раз)")
    p.add_argument("--judge", action="append", help="судья в виде «Суд_Фамилия И.О.» (только ARB; см. dict)")
    p.add_argument("--status", action="append",
                   help="статус документа: current (действующие), lost (утратили силу), pending (не вступили) или название")
    p.add_argument("--court", default="arb", choices=["arb", "soj"], help="для --dispute/--claim: арбитражные (arb) или общей юрисдикции (soj)")
    p.add_argument("--dispute", action="append", help="категория спора (только ARB; варианты — команда dispute)")
    p.add_argument("--claim", action="append", help="требование внутри ОДНОЙ --dispute (только ARB)")
    p.add_argument("--raw", action="append", help="готовое значение поля: ПОЛЕ=ЗНАЧЕНИЕ")
    p.add_argument("--list", type=int, default=0, help="сразу вернуть N документов из основного узла")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("quick", help="быстрый поиск: естественный запрос или цитата («5.35.1 КоАП»), смешанная выдача")
    p.add_argument("--text", required=True)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_quick)

    p = sub.add_parser("dispute", help="категории спора и требования (раздел ARB) с кодами")
    p.add_argument("--court", default="arb", choices=["arb", "soj"])
    p.add_argument("--filter", default="", help="часть названия")
    p.add_argument("--claims-for", help="код категории: показать её требования")
    p.set_defaults(fn=cmd_dispute)

    p = sub.add_parser("tree", help="поиск по деревьям-справочникам (тематика, результат рассмотрения)")
    p.add_argument("--div", default="LAW")
    p.add_argument("--field", required=True, help="topic (LAW) или result (ARB)")
    p.add_argument("--filter", required=True, help="часть названия узла")
    p.set_defaults(fn=cmd_tree)

    p = sub.add_parser("list", help="документы найденного (постранично, по узлу дерева)")
    p.add_argument("--search", required=True, help="search_id из команды search")
    p.add_argument("--node", help="узел дерева: код базы (ARB), название или node из search")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("edition", help="актуальная редакция документа, список редакций, редакция на дату")
    p.add_argument("--base", default="LAW")
    p.add_argument("--n", required=True, type=int)
    p.add_argument("--all", action="store_true", help="список редакций")
    p.add_argument("--limit", type=int, default=15, help="сколько редакций показать (новейшие)")
    p.add_argument("--on-date", help="редакция, действовавшая на ДД.ММ.ГГГГ")
    p.set_defaults(fn=cmd_edition)

    p = sub.add_parser("export", help="выгрузка документа (по умолчанию FB2, актуальной редакции)")
    p.add_argument("--base", default="LAW")
    p.add_argument("--n", required=True, type=int)
    p.add_argument("--format", default="fb2", choices=["fb2", "utxt", "html", "docx", "rtf", "epub", "pdf", "xml2003"])
    p.add_argument("--exact", action="store_true", help="не подменять номер актуальной редакцией")
    p.add_argument("--on-date", help="выгрузить редакцию, действовавшую на ДД.ММ.ГГГГ")
    p.add_argument("--out", help="каталог для файла (по умолчанию .cache/consultant/exports)")
    p.add_argument("--split", action="store_true", help="сразу разобрать в каталог markdown (skill legal-doc-splitter)")
    p.add_argument("--split-dir", help="куда класть разобранный каталог")
    p.set_defaults(fn=cmd_export)
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
    except ConsultantError as e:
        print(json.dumps(e.to_dict(), ensure_ascii=False, indent=indent), file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # непредвиденное: тоже JSON, чтобы агент не разбирал трассировку
        print(json.dumps({"error": {"code": "internal_error", "message": "%s: %s" % (type(e).__name__, e)}},
                         ensure_ascii=False, indent=indent), file=sys.stderr)
        return 1
