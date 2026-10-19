# -*- coding: utf-8 -*-
"""Клиент КонсультантПлюс (cloud.consultant.ru): поиск, список, редакции, экспорт, справочники.

Главное:
  * все команды — XHR на один CGI, обязателен заголовок X-Requested-With;
  * на учётную запись разрешено ограниченное число одновременных подключений, поэтому каждая
    команда занимает слот (refresh2 op=activate) и ВСЕГДА освобождает его в конце (slot());
  * документ идентифицируется номером редакции: «публичный» номер ведёт на первоначальный текст,
    актуальный номер даёт content=edition, редакцию на дату — content=editions.
"""

import hashlib
import html as htmllib
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

from . import paths
from .auth import CGI
from .errors import (AuthRequired, IncompleteExport, NotFound, ServiceError, SlotBusy, UsageError)

DAY = 86400
PAGE = 50  # сервер всегда отдаёт список порциями по 50
FIELD_ALIASES = {
    "text": "COMMON_TEXT", "name": "COMMON_DOCNAME", "docname": "COMMON_DOCNAME", "number": "COMMON_NUMBER",
    "date": "COMMON_DATE", "organ": "COMMON_ORGAN", "authority": "COMMON_ORGAN", "doctype": "COMMON_VID_DOC",
    "type": "COMMON_VID_DOC", "topic": "COMMON_RUBRICS", "rubric": "COMMON_RUBRICS", "status": "COMMON_STATUS",
    "received": "COMMON_RECEIVE", "judge": "COMMON_JUDGE", "inn": "COMMON_INN", "result": "COMMON_CASE_RESULT",
}
DATE_MODES = {"exact": "r1", "range": "r2", "before": "r3", "later": "r4"}
EXPORT_EXT = {"utxt": "txt", "xml2003": "xml", "mpdf": "pdf"}
EDITION_STATUS = {2: "future", 4: "current", 5: "past"}


def strip_tags(s):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def parse_json(r):
    """JSON-ответ команды; ошибки сервиса превращаются в исключения клиента."""
    try:
        j = r.json()
    except ValueError:
        raise ServiceError("Ответ сервиса не JSON (HTTP %s, %s)." % (r.status_code, r.headers.get("Content-Type")),
                           code="bad_response")
    if isinstance(j, dict) and j.get("err"):
        e = j["err"][0] if isinstance(j["err"], list) and j["err"] else {}
        code, msg = e.get("code"), e.get("_text") or e.get("title") or "ошибка сервиса"
        if code == "max_connections_reached":
            raise SlotBusy(msg, data=(e.get("message") or {}).get("data"))
        raise ServiceError(msg, code=code or "service_error")
    return j


def to_iso(date_text):
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", date_text.strip())
    if not m:
        raise UsageError("Дата должна быть в формате ДД.ММ.ГГГГ: %r" % date_text)
    return "%s-%s-%s" % (m.group(3), m.group(2), m.group(1))


def flatten_tree(tp):
    out = []

    def walk(n, depth):
        if n.get("treeUID"):
            out.append({"node": n["treeUID"], "level": depth, "name": n.get("name"), "div": n.get("div"),
                        "base": n.get("base") or None, "count": n.get("ndoc"), "selected": bool(n.get("selected"))})
        for c in n.get("nodes") or []:
            walk(c, depth + 1)

    for c in (tp or {}).get("nodes") or []:
        walk(c, 1)
    return out


def parse_items(j):
    out = []
    for pane in (j.get("list") or {}).get("listpanes") or []:
        for it in pane.get("items") or []:
            parts = it.get("title") or []
            req = " ".join(strip_tags(x.get("html")) for x in parts if x.get("class") == "T")
            ttl = " ".join(strip_tags(x.get("html")) for x in parts if x.get("class") == "TH")
            rest = " ".join(strip_tags(x.get("html")) for x in parts if x.get("class") not in ("T", "TH"))
            snip = " ".join(strip_tags(x.get("html")) for x in it.get("extraTitle") or [])
            out.append({"order": it.get("order"), "base": it.get("base"), "n": it.get("nd"),
                        "requisites": req or rest, "title": ttl, "snippet": (snip[:400] or None)})
    return out


def norm_edition(e):
    redd = e.get("reddate") or ""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", redd)
    return {"number": e.get("number"), "n": e.get("nd"), "act_date": e.get("edDate"),
            "effective_from": ("%s-%s-%s" % (m.group(3), m.group(2), m.group(1))) if m else None,
            "status": EDITION_STATUS.get(e.get("status"), str(e.get("status"))),
            "never_in_force": "не действовала" in redd, "partial": "за исключением" in redd,
            "note": e.get("_text")}


def check_export(fmt, body, content_length, encoding):
    c = {"nonempty": len(body) > 0}
    if content_length and not encoding:
        c["length_matches_header"] = int(content_length) == len(body)
    need = []
    if fmt == "fb2":
        c["starts_xml"] = body[:5] == b"<?xml"
        c["closed"] = body.rstrip().endswith(b"</FictionBook>")
        try:
            ET.fromstring(body)
            c["well_formed"] = True
        except ET.ParseError:
            c["well_formed"] = False
        c["footer_present"] = "Документ предоставлен".encode("utf-8") in body  # информативно
        need = ["starts_xml", "closed", "well_formed"]
    elif fmt in ("html", "docx", "epub"):
        c["zip"] = body[:2] == b"PK"
        need = ["zip"]
    elif fmt == "utxt":
        c["utf16_bom"] = body[:2] in (b"\xff\xfe", b"\xfe\xff")
        need = ["utf16_bom"]
    elif fmt in ("pdf", "mpdf"):
        c["pdf"] = body[:4] == b"%PDF"
        need = ["pdf"]
    c["ok"] = c["nonempty"] and all(c.get(k) for k in need) and c.get("length_matches_header", True)
    return c


class Client:
    def __init__(self, state, http, cache):
        self.state, self.http, self.cache = state, http, cache
        self.rnd = state["rnd"]
        self._depth = 0

    # ----- транспорт ---------------------------------------------------------

    def _headers(self):
        return {"Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "%s?req=card&rnd=%s" % (CGI, self.rnd)}

    def _post(self, data):
        body = dict(data)
        body["rnd"] = self.rnd
        return self.http.request("POST", CGI, params={"rnd": self.rnd}, data=body, headers=self._headers())

    def _get(self, params):
        p = dict(params)
        p["rnd"] = self.rnd
        return self.http.request("GET", CGI, params=p, headers=self._headers())

    def _refresh(self, op):
        r = self.http.request("POST", CGI, params={"req": "refresh2", "cnt": 0, "op": op, "rnd": self.rnd},
                              headers=self._headers())
        return parse_json(r)

    # ----- слот подключения --------------------------------------------------

    def activate(self):
        j = self._refresh("activate")
        if "session" not in j:  # недействительная сессия отвечает пустым {}
            raise AuthRequired("Сессия недействительна или истекла.")
        return j["session"]

    def deactivate(self):
        return self._refresh("deactivate")

    @contextmanager
    def slot(self):
        """Занять слот на время блока и гарантированно освободить."""
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        self.activate()
        self._depth = 1
        try:
            yield self
        finally:
            self._depth = 0
            try:
                self.deactivate()
            except Exception:
                pass

    # ----- карточка и справочники --------------------------------------------

    def card_fields(self, div):
        key = "v2|fields|" + div
        cached = self.cache.get("card", key, 7 * DAY)
        if cached:
            return cached
        j = parse_json(self._post({"req": "card", "cardDiv": div, "fields": "1"}))
        raw = j.get("fields") or []
        out = {"div": div, "main_base": j.get("mainBase"),
               "fields": [{"num": f["num"], "name": f.get("t"), "pseudo": f.get("pseudo"), "type": f.get("type"),
                           "dictionary": bool(f.get("hasFilter")), "tree": bool(f.get("isTree")),
                           "use_code": bool(f.get("useCode"))}
                          for f in raw if "num" in f],
               "unsupported": [f.get("t") for f in raw if "num" not in f]}
        self.cache.set("card", key, out)
        return out

    def field(self, div, key):
        defs = self.card_fields(div)["fields"]
        k = str(key).strip().lower()
        pseudo = FIELD_ALIASES.get(k)
        for f in defs:
            if (pseudo and f["pseudo"] == pseudo) or str(f["num"]) == k or (f["name"] or "").lower() == k:
                return f
        raise UsageError("В разделе %s нет поля «%s»." % (div, key),
                         hint="Доступные поля: " + ", ".join("%s (%s)" % (f["name"], f["pseudo"]) for f in defs))

    def dict_values(self, div, num, filter_text="", limit=100):
        key = "v3|%s|%s|%s|%d" % (div, num, filter_text.lower(), limit)
        cached = self.cache.get("dict", key, DAY)
        if cached is not None:
            return cached
        main_base = self.card_fields(div).get("main_base") or div
        items, index, page = [], 0, 500
        while len(items) < limit:
            j = parse_json(self._post({"req": "dict", "div": div, "n": num, "index": index, "count": page,
                                       "filter": filter_text, "fltType": "fltPart", "EXCL": "", "mainBase": main_base}))
            chunk = j.get("items") or []
            # Значение для поиска — «Краткое_Полное». Без фильтра сервер отдаёт его готовым (values),
            # с фильтром — только name и secondName, поэтому собираем сами.
            for it in chunk:
                kw, vals = it.get("keyword"), it.get("values")
                one = (str(kw) if kw is not None else None) or (vals[0] if vals else None) or                     ("%s_%s" % (it["name"], it["secondName"]) if it.get("secondName") else it.get("name"))
                items.append({"name": it.get("name"), "full": it.get("secondName"), "value": one,
                              # у элемента может быть несколько значений («Закон» = «Закон» + «Законопроект»)
                              "values": [str(kw)] if kw is not None else (vals or [one])})
            if len(chunk) < page:
                break
            index += page
        items = items[:limit]
        self.cache.set("dict", key, items)
        return items

    def tree_find(self, div, f, text):
        """Узлы дерева-справочника (тематика, результат рассмотрения), совпавшие с текстом.

        Сервер при filter отдаёт урезанное дерево (предки и все совпавшие узлы), но битом 512 в status
        помечает только ПЕРВОЕ совпадение, поэтому совпадения ищем по имени узла. Значение для поиска —
        числовой код узла (несколько — через запятую)."""
        key = "v2|tree|%s|%s|%s" % (div, f["num"], text.lower())
        cached = self.cache.get("dict", key, DAY)
        if cached is not None:
            return cached
        main_base = self.card_fields(div).get("main_base") or div
        j = parse_json(self._post({"req": "tree", "treeid": "%s_%s_%s" % (div, f["num"], main_base), "itemid": "0",
                                   "filter": text, "fltType": "fltPart", "EXCL": "", "div": div,
                                   "mainBase": main_base}))
        out, low = [], text.lower()

        def walk(node, path):
            for k in node.get("children") or []:
                name = (k.get("name") or "").strip()
                if low in name.lower():
                    out.append({"id": k["id"], "name": name, "path": path, "leaf": k.get("type") == "l"})
                walk(k, path + [name])

        walk(j.get("tree") or {}, [])
        self.cache.set("dict", key, out)
        return out

    # ----- поиск и список ----------------------------------------------------

    def dispute_find(self, court, text="", claims_for=None):
        """Категории спора (court: arb|soj) или требования внутри категории (claims_for — код категории).

        С filter сервер отдаёт в right плоский список всех совпавших категорий по всем группам."""
        field = 211 if court == "arb" else 210
        main_base = self.card_fields("ARB").get("main_base") or "ARB"
        if claims_for:
            data = {"req": "dict", "dn": "claims", "disputeRubr": str(claims_for), "base": main_base,
                    "field": field, "left": "1", "filter": text}
        else:
            data = {"req": "dict", "dn": "disputeCategory", "base": main_base, "field": field, "left": "1",
                    "filter": text}
        j = parse_json(self._post(data))
        return {"court": court, "field": field,
                "groups": [{"code": x["value"], "name": x["text"]} for x in j.get("left") or []],
                "items": [{"code": x["value"], "name": x["text"]} for x in j.get("right") or []]}

    def _pick_dispute(self, found, name):
        cands = found["items"] + [g for g in found["groups"] if g["code"] not in {i["code"] for i in found["items"]}]
        exact = [x for x in cands if x["name"].lower() == name.lower()]
        pick = exact[0] if len(exact) == 1 else (cands[0] if len(cands) == 1 else None)
        if not pick:
            raise UsageError("Значение «%s» не найдено однозначно." % name,
                             hint="Уточните название; команда dispute покажет варианты с кодами.",
                             data={"candidates": cands[:10]})
        return pick

    def _encode_dispute(self, cond):
        court = cond.get("court") or "arb"
        field = 211 if court == "arb" else 210
        if cond["field"] == "dispute":
            codes = [self._pick_dispute(self.dispute_find(court, nm), nm)["code"] for nm in cond["names"]]
        else:  # claim: категория нужна только как контекст поиска, в запрос идут коды требований
            cat = self._pick_dispute(self.dispute_find(court, cond["category"]), cond["category"])
            codes = [self._pick_dispute(self.dispute_find(court, nm, claims_for=cat["code"]), nm)["code"]
                     for nm in cond["names"]]
        return field, ",".join(str(c) for c in codes)

    def _resolve_tree(self, div, f, names):
        ids = []
        for nm in names:
            found = self.tree_find(div, f, nm)
            exact = [m for m in found if m["name"].lower() == nm.lower()]
            pick = exact[0] if len(exact) == 1 else (found[0] if len(found) == 1 else None)
            if not pick:
                raise UsageError("Значение «%s» не найдено однозначно в «%s»." % (nm, f["name"]),
                                 hint="Уточните название; команда tree покажет варианты с путями.",
                                 data={"candidates": [{"id": m["id"], "name": m["name"], "path": m["path"]}
                                                      for m in found[:10]]})
            ids.append(str(pick["id"]))
        return ids

    def _resolve_names(self, div, f, names):
        out = []
        for nm in names:
            items = self.dict_values(div, f["num"], nm, limit=50)
            low = nm.lower()
            exact = [i for i in items if (i["name"] or "").lower() == low or (i["full"] or "").lower() == low]
            pick = exact[0] if exact else (items[0] if len(items) == 1 else None)
            if pick:
                out.extend(pick["values"])
            elif f["pseudo"] == "COMMON_NUMBER":
                out.append(nm)  # номер вводится как есть
            else:
                raise UsageError("Значение «%s» не найдено однозначно в справочнике «%s»." % (nm, f["name"]),
                                 hint="Уточните название; команда dict покажет варианты.",
                                 data={"candidates": [i["name"] for i in items[:10]]})
        return out

    def encode(self, div, cond):
        f = self.field(div, cond["field"])
        if "raw" in cond:
            return f["num"], cond["raw"]
        if f["type"] == "text":
            return f["num"], "%s,%s" % (cond.get("mode") or "b", cond["text"])
        if f["type"] == "date":
            mode = DATE_MODES[cond["mode"]]
            dates = [cond["date"]] + ([cond["date2"]] if mode == "r2" else [])
            return f["num"], "%s,%s" % (mode, ",".join(dates))
        if f["tree"]:
            return f["num"], ",".join(self._resolve_tree(div, f, cond["names"]))
        if f["type"] == "string" and f["use_code"]:  # статус: коды из справочника через запятую
            return f["num"], ",".join(self._resolve_names(div, f, cond["names"]))
        if f["type"] == "string" and f["dictionary"]:
            return f["num"], "|" + "|".join(self._resolve_names(div, f, cond["names"])) + "|"
        raise UsageError("Поле «%s» (тип %s) не задаётся через именованные параметры." % (f["name"], f["type"]),
                         hint="Передайте готовое значение: --raw %s=<закодированное значение>." % f["num"])

    def search(self, div, conds):
        data = {"req": "query", "content": "cellar", "cardDiv": div, "NOQUERYLOG": "1"}  # не писать в журнал поисков
        described = []
        for c in conds:
            num, enc = self.encode(div, c) if c["field"] not in ("dispute", "claim") else self._encode_dispute(c)
            if str(num) in data and c["field"] in ("dispute", "claim"):
                enc = data[str(num)] + "," + enc
            data[str(num)] = enc
            described.append({"field": c["field"], "param": "%s=%s" % (num, enc)})
        return self._run_search(div, data, described)

    def _run_search(self, div, data, described=None):
        j = parse_json(self._post(data))
        tp = j.get("treepane") or {}
        if not j.get("cacheid"):
            return {"id": None, "div": div, "total": 0, "nodes": [], "conditions": described or []}
        sid = hashlib.sha1(json.dumps([div, data], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
        rec = {"id": sid, "div": div, "request": data, "cacheid": j["cacheid"], "created": time.time(),
               "total": tp.get("total"), "nodes": flatten_tree(tp), "conditions": described or []}
        self.cache.put_named("searches", sid, rec)
        return rec

    def load_search(self, sid):
        rec = self.cache.get_named("searches", sid)
        if not rec:
            raise NotFound("Поиск %s не найден в кеше." % sid, hint="Повторите команду search.")
        return rec

    def pick_node(self, rec, node=None):
        nodes = [n for n in rec["nodes"] if n["count"]]
        if not nodes:
            raise NotFound("В результатах поиска нет документов.")
        if node is None:
            return next((n for n in nodes if n["selected"]), nodes[0])
        q = str(node).lower()
        for pred in (lambda n: n["node"] == node, lambda n: (n["base"] or "").lower() == q,
                     lambda n: (n["name"] or "").lower() == q, lambda n: q in (n["name"] or "").lower()):
            hit = [n for n in nodes if pred(n)]
            if len(hit) == 1:
                return hit[0]
            if len(hit) > 1:
                raise UsageError("Узел «%s» неоднозначен." % node,
                                 data={"candidates": [{"node": n["node"], "name": n["name"], "base": n["base"]}
                                                      for n in hit[:10]]})
        raise UsageError("Узел «%s» не найден в дереве результатов." % node)

    def list(self, sid, node=None, offset=0, limit=20):
        rec = self.load_search(sid)
        nd = self.pick_node(rec, node)
        start = (offset // PAGE) * PAGE
        items, pos, retried = [], start, False
        while len(items) < (offset - start) + limit:
            data = {"req": "query", "content": "list", "mode": "searchcard", "cacheid": rec["cacheid"],
                    "BASENODE": nd["node"], "showall": "0", "NOQUERYLOG": "1",
                    "RANGE": "%d-%d" % (pos, pos + PAGE - 1)}
            j = parse_json(self._post(data))
            if "list" not in j:
                if retried:
                    raise ServiceError("Сервис не вернул список (возможно, поиск устарел).", code="list_failed")
                rec = self._run_search(rec["div"], rec["request"], rec.get("conditions"))  # перестроить поиск
                retried = True
                continue
            chunk = parse_items(j)
            items += chunk
            if len(chunk) < PAGE:
                break
            pos += PAGE
        sliced = items[offset - start: offset - start + limit]
        return {"search_id": rec["id"], "node": {"node": nd["node"], "name": nd["name"], "base": nd["base"]},
                "total_in_node": nd["count"], "offset": offset, "count": len(sliced), "items": sliced}

    def quick(self, text, limit=50):
        """Быстрый поиск («поиск плюс»): ранжированная смешанная выдача по естественному запросу или цитате
        («5.35.1 КоАП» -> сам кодекс, затем комментарии и ситуации). Блоки — типы материалов."""
        j = parse_json(self._post({"req": "query", "mode": "splus", "content": "list",
                                   "SEARCHPLUS": text[:500], "NOQUERYLOG": "1"}))
        lst = j.get("list") or {}
        out = []
        for pane in lst.get("listpanes") or []:
            block = pane.get("block")
            items = pane.get("items") or (block or {}).get("items") or []
            for it in parse_items({"list": {"listpanes": [{"items": items}]}}):
                it["block"] = (block or {}).get("name")
                out.append(it)
        return {"query": text, "total": lst.get("n"), "page_size": lst.get("pageSize"), "returned": min(len(out), limit),
                "items": out[:limit], "advice_search_in_arb": bool(lst.get("adviseSearchInARB"))}

    # ----- редакции ----------------------------------------------------------

    def edition(self, base, n):
        j = parse_json(self._get({"req": "doc", "base": base, "n": n, "content": "edition"}))
        return {"requested": int(n), "current": int(j.get("d") or n), "is_current": not j.get("isN"),
                "ineffective": bool(j.get("isIneffective"))}

    def editions(self, base, n):
        cur = self.edition(base, n)["current"]
        j = parse_json(self._get({"req": "doc", "base": base, "n": cur, "content": "editions"}))
        eds = [norm_edition(e) for e in (j.get("editions") or {}).get("list") or []]
        return {"current_n": cur, "count": len(eds), "editions": eds}

    def edition_on_date(self, base, n, on_date):
        iso = to_iso(on_date)
        eds = self.editions(base, n)["editions"]
        cands = [e for e in eds if e["status"] in ("current", "past") and not e["never_in_force"]
                 and e["effective_from"] and e["effective_from"] <= iso]
        if not cands:
            raise NotFound("Не найдена редакция, действовавшая на %s." % on_date)
        return max(cands, key=lambda e: (e["effective_from"], e["number"]))

    # ----- экспорт -----------------------------------------------------------

    def export(self, base, n, fmt="fb2", out_dir=None, exact=False, on_date=None):
        if on_date:
            ed = self.edition_on_date(base, n, on_date)
            nd, resolution = ed["n"], {"mode": "on_date", "date": on_date, "edition": ed}
        elif exact:
            nd, resolution = int(n), {"mode": "exact"}
        else:
            e = self.edition(base, n)
            nd, resolution = e["current"], dict(e, mode="current")
        r = self._post({"req": "export", "type": fmt, "base": base, "n": str(nd), "page": "text"})
        if "json" in (r.headers.get("Content-Type") or ""):
            parse_json(r)  # ошибка сервиса (например, лимит) поднимется здесь
            raise ServiceError("Экспорт вернул JSON вместо файла.", code="export_bad_response")
        body = r.content
        checks = check_export(fmt, body, r.headers.get("Content-Length"), r.headers.get("Content-Encoding"))
        if not checks["ok"]:
            raise IncompleteExport("Выгрузка не прошла проверку целостности.", data=checks,
                                   hint="Повторите; если повторяется — выгрузить в другом формате (utxt).")
        out = Path(out_dir) if out_dir else self.cache.root / "exports"
        out.mkdir(parents=True, exist_ok=True)
        path = out / ("%s_%s.%s" % (base, nd, EXPORT_EXT.get(fmt, fmt)))
        path.write_bytes(body)
        title = None
        if fmt == "fb2":
            m = re.search(rb"<book-title>(.*?)</book-title>", body[:6000], re.S)
            title = htmllib.unescape(m.group(1).decode("utf-8", "replace")).replace("\n", " ").strip() if m else None
        ed_date = None
        if title:
            m = re.search(r"\(ред\. от (\d\d\.\d\d\.\d{4})\)", title)
            ed_date = m.group(1) if m else None
        return {"path": str(path), "format": fmt, "bytes": len(body), "base": base, "n": nd,
                "requested_n": int(n), "title": title, "edition_date": ed_date, "resolution": resolution,
                "checks": checks}

    def split(self, exported, out_dir=None):
        """Разбор FB2 в каталог markdown через skill legal-doc-splitter."""
        if exported["format"] != "fb2":
            raise UsageError("Разбор в каталог поддерживает только формат fb2.")
        if not paths.SPLITTER.exists():
            raise UsageError("Skill legal-doc-splitter не найден: %s" % paths.SPLITTER)
        suffix = ("_red-" + "-".join(reversed(exported["edition_date"].split(".")))) if exported["edition_date"] else ""
        out = Path(out_dir) if out_dir else self.cache.root / "docs" / ("%s-%s%s" % (exported["base"], exported["n"], suffix))
        p = subprocess.run([sys.executable, "-X", "utf8", str(paths.SPLITTER), "split", exported["path"],
                            "--out-dir", str(out), "--base", exported["base"], "--n", str(exported["n"]), "--force"],
                           capture_output=True, text=True, encoding="utf-8")
        if p.returncode != 0:
            raise ServiceError("Разбор документа не удался: " + (p.stderr or p.stdout).strip()[-300:], code="split_failed")
        ix = json.loads((out / "index.json").read_text(encoding="utf-8"))
        return {"dir": str(out), "readme": str(out / "README.md"), "stats": ix["stats"], "validation": ix["validation"]}
