# -*- coding: utf-8 -*-
"""Командная строка science-search.

Контракт: успех — JSON в stdout, код 0; ошибка — JSON {"error": {code, message, hint}} в stderr и код возврата
(2 неверные параметры, 6 сеть, 7 не найдено). Инструмент сам ничего не сохраняет в docs/refs: файлы попадают в
.cache/science/ или в --out.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import requests

from . import paths
from .errors import NetworkError, NotFound, ScienceError, SourceSkipped, UsageError
from .fetch import fetch_work, ingest_file, manual_markdown, refs_suggestion
from .http import Http
from .model import access_status, fuse, new_work, public_view
from .sources import REGISTRY, Opts, lookup_doi


def _cache():
    return paths.cache_dir()


def cmd_sources(a):
    out = []
    for s in REGISTRY.values():
        import os
        out.append({"name": s.name, "default": s.default, "note": s.note,
                    "needs_key": s.key_env, "key_set": bool(os.environ.get(s.key_env)) if s.key_env else None,
                    "verified_live": s.verified})
    return {"sources": out,
            "not_queried_directly": {
                "eLIBRARY": "парсинг запрещён лицензией; статьи приходят через OpenAlex/Crossref, загрузка вручную по подписке пользователя",
                "КиберЛенинка": "поиск, API и PDF закрыты robots.txt; ссылки приходят через OpenAlex, текст со страницы статьи извлекается, если robots разрешает"},
            "env": {"SCIENCE_MAILTO": "необязательно: контакт для «вежливых» очередей OpenAlex/Crossref (ставит пользователь)",
                    "S2_API_KEY": "ключ Semantic Scholar", "CORE_API_KEY": "ключ CORE"}}


def cmd_search(a):
    names = [n.strip() for n in a.sources.split(",")] if a.sources else [n for n, s in REGISTRY.items() if s.default]
    bad = [n for n in names if n not in REGISTRY]
    if bad:
        raise UsageError("Неизвестные источники: %s." % ", ".join(bad), hint="Список: команда sources.")
    o = Opts(lang=None if a.lang == "any" else a.lang, from_year=a.from_year, to_year=a.to_year, oa_only=a.oa_only,
             limit=a.per_source, types=a.type, sort=a.sort)
    http = Http(min_interval=a.min_interval)
    lists, report = [], {n: {"count": 0} for n in names}
    for q in a.query:
        for n in names:
            try:
                works = REGISTRY[n].fn(http, q, o)
                lists.append(works)
                report[n]["count"] += len(works)
            except SourceSkipped as e:
                report[n]["skipped"] = str(e)
            except (requests.RequestException, NetworkError, ScienceError) as e:
                report[n]["error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    fused = fuse(lists)
    if a.oa_only:
        fused = [w for w in fused if access_status(w) == "open"]
    if a.sort == "year":
        fused.sort(key=lambda w: -(w.get("year") or 0))
    elif a.sort == "citations":
        fused.sort(key=lambda w: -(w.get("cited_by") or 0))
    fused = fused[: a.limit]
    sid = hashlib.sha1(json.dumps([a.query, names, vars(o)], ensure_ascii=False, sort_keys=True, default=str)
                       .encode("utf-8")).hexdigest()[:8]
    (_cache() / "searches").mkdir(exist_ok=True)
    (_cache() / "searches" / (sid + ".json")).write_text(json.dumps({"works": fused}, ensure_ascii=False), encoding="utf-8")
    return {"search_id": sid, "queries": a.query, "sources": report, "total": len(fused),
            "open_access": sum(1 for w in fused if access_status(w) == "open"),
            "results": [public_view(w, i, a.abstract_chars) for i, w in enumerate(fused, 1)],
            "next": "fetch --search %s --pick open (или номера через запятую): скачать открытые тексты; "
                    "остальные попадут в список «скачать вручную»" % sid}


def cmd_get(a):
    w = lookup_doi(Http(min_interval=a.min_interval), a.doi)
    if not w:
        raise NotFound("DOI %s не найден в OpenAlex." % a.doi, hint="Проверьте DOI или найдите работу командой search.")
    v = public_view(w, 1, 3000)
    v["keywords"] = w["keywords"]
    v["candidates"] = [{"url": c["url"], "kind": c["kind"], "open": c["open"], "source": c["source"]} for c in w["candidates"]]
    return v


def _parse_pick(spec, works):
    if spec == "all":
        return list(range(1, len(works) + 1))
    if spec == "open":
        return [i for i, w in enumerate(works, 1) if access_status(w) != "closed"]
    try:
        picks = sorted({int(x) for x in spec.split(",") if x.strip()})
    except ValueError:
        raise UsageError("--pick: номера через запятую, либо open, либо all.")
    bad = [p for p in picks if p < 1 or p > len(works)]
    if bad:
        raise UsageError("Нет работ с номерами %s (в поиске их %d)." % (bad, len(works)))
    return picks


def cmd_fetch(a):
    http = Http(min_interval=a.min_interval)
    works = []
    if a.search:
        p = _cache() / "searches" / (a.search + ".json")
        if not p.exists():
            raise NotFound("Поиск %s не найден в кеше." % a.search, hint="Повторите команду search.")
        pool = json.loads(p.read_text(encoding="utf-8"))["works"]
        works += [(i, pool[i - 1]) for i in _parse_pick(a.pick or "open", pool)]
    for d in a.doi or []:
        w = lookup_doi(http, d)
        if not w:
            raise NotFound("DOI %s не найден в OpenAlex." % d)
        works.append((None, w))
    if not works:
        raise UsageError("Укажите --search (с --pick) или --doi.")
    out_dir = Path(a.out) if a.out else _cache() / "files"
    downloaded, manual = [], []
    for n, w in works:
        status, info = fetch_work(http, w, out_dir, want_text=not a.no_text)
        if status == "downloaded":
            info["n"], info["title"] = n, w["title"]
            info["refs_entry_suggestion"] = refs_suggestion(w, info)
            downloaded.append(info)
        else:
            info["n"] = n
            manual.append(info)
    res = {"out_dir": str(out_dir), "downloaded": downloaded, "manual": manual}
    if manual:
        res["manual_downloads_file"] = manual_markdown(manual, out_dir)
        res["tell_user"] = ("%d материал(ов) не удалось загрузить автоматически (подписка, запрет robots.txt или нет открытой "
                            "ссылки): список со ссылками в manual_downloads_file; пользователь может скачать их под своей "
                            "подпиской и подключить командой ingest." % len(manual))
    return res


def cmd_ingest(a):
    src = Path(a.file)
    if not src.exists():
        raise NotFound("Файл не найден: %s" % src)
    w = lookup_doi(Http(min_interval=a.min_interval), a.doi) if a.doi else None
    if w is None:
        w = new_work("user", title=a.title or src.stem, doi=a.doi,
                     authors=[x.strip() for x in (a.authors or "").split(";") if x.strip()],
                     year=a.year, venue=a.venue)
    else:  # поля, заданные вручную, главнее
        for k, v in (("title", a.title), ("year", a.year), ("venue", a.venue)):
            if v:
                w[k] = v
        if a.authors:
            w["authors"] = [x.strip() for x in a.authors.split(";") if x.strip()]
    out_dir = Path(a.out) if a.out else _cache() / "files"
    info = ingest_file(src, out_dir, w, want_text=not a.no_text)
    info["refs_entry_suggestion"] = refs_suggestion(w, info)
    return info


def build_parser():
    ap = argparse.ArgumentParser(prog="science.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--min-interval", type=float, default=0.5, help="пауза между запросами, с")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sources", help="источники, ключи, что не опрашивается напрямую").set_defaults(fn=cmd_sources)

    p = sub.add_parser("search", help="поиск по ключевым словам во всех источниках; результаты сливаются и ранжируются")
    p.add_argument("--query", action="append", required=True, help="ключевые слова (можно несколько раз: синонимы, русский и английский)")
    p.add_argument("--lang", default="ru", choices=["ru", "en", "any"])
    p.add_argument("--from-year", type=int)
    p.add_argument("--to-year", type=int)
    p.add_argument("--oa-only", action="store_true", help="только с открытым доступом")
    p.add_argument("--type", action="append", help="тип: article, book, book-chapter, dissertation, ... (можно несколько раз)")
    p.add_argument("--sources", help="через запятую; по умолчанию openalex,crossref,doaj,openaire")
    p.add_argument("--per-source", type=int, default=25, help="сколько брать у каждого источника на запрос")
    p.add_argument("--limit", type=int, default=25, help="сколько вернуть после слияния")
    p.add_argument("--sort", default="relevance", choices=["relevance", "year", "citations"])
    p.add_argument("--abstract-chars", type=int, default=400)
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("get", help="метаданные одной работы по DOI")
    p.add_argument("--doi", required=True)
    p.set_defaults(fn=cmd_get)

    p = sub.add_parser("fetch", help="скачать полные тексты открытого доступа; остальное — в список «скачать вручную»")
    p.add_argument("--search", help="search_id из команды search")
    p.add_argument("--pick", help="номера из результатов через запятую, open (по умолчанию) или all")
    p.add_argument("--doi", action="append", help="скачать по DOI (можно несколько раз)")
    p.add_argument("--out", help="каталог (по умолчанию .cache/science/files)")
    p.add_argument("--no-text", action="store_true", help="не извлекать текст из PDF")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("ingest", help="подключить PDF, который пользователь скачал вручную (метаданные, текст)")
    p.add_argument("--file", required=True)
    p.add_argument("--doi")
    p.add_argument("--title")
    p.add_argument("--authors", help="через точку с запятой")
    p.add_argument("--year", type=int)
    p.add_argument("--venue")
    p.add_argument("--out")
    p.add_argument("--no-text", action="store_true")
    p.set_defaults(fn=cmd_ingest)
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
    except ScienceError as e:
        print(json.dumps(e.to_dict(), ensure_ascii=False, indent=indent), file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(json.dumps({"error": {"code": "internal_error", "message": "%s: %s" % (type(e).__name__, e)}},
                         ensure_ascii=False, indent=indent), file=sys.stderr)
        return 1
