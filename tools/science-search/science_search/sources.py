# -*- coding: utf-8 -*-
"""Адаптеры источников. Каждый возвращает список работ в единой модели (model.new_work).

Проверены вживую: openalex, crossref, doaj, openaire. Требуют ключа и не проверялись без него: core
(CORE_API_KEY), semanticscholar (S2_API_KEY, без ключа часто 429).
eLIBRARY и КиберЛенинка не опрашиваются напрямую: у eLIBRARY парсинг запрещён лицензией, у КиберЛенинки
поиск и API закрыты robots.txt. Их статьи приходят через OpenAlex/OpenAIRE и при невозможности загрузки
попадают в список «скачать вручную».
"""

import os
from urllib.parse import quote

from .errors import SourceSkipped
from .model import candidate, guess_lang, new_work, norm_doi, reconstruct_abstract, strip_tags


class Opts:
    def __init__(self, lang=None, from_year=None, to_year=None, oa_only=False, limit=20, types=None, sort="relevance"):
        self.lang, self.from_year, self.to_year = lang, from_year, to_year
        self.oa_only, self.limit, self.types, self.sort = oa_only, limit, types or [], sort


def _mailto():
    return os.environ.get("SCIENCE_MAILTO")


def _year_ok(w, o):
    y = w.get("year")
    if y is None:
        return True
    return (not o.from_year or y >= o.from_year) and (not o.to_year or y <= o.to_year)


def _lang_ok(w, o):
    if not o.lang:
        return True
    declared = (w.get("language") or "").lower()[:2]
    if declared and declared not in ("un", "zx"):
        return declared == o.lang
    g = guess_lang(w.get("title"))
    return g is None or g == o.lang


def _filter(works, o):
    return [w for w in works if _year_ok(w, o) and _lang_ok(w, o)]


def _int(x):
    try:
        return int(str(x)[:4])
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# OpenAlex — основной источник: метаданные по языку, году, типу, открытому доступу; адреса полных текстов
# --------------------------------------------------------------------------

OPENALEX_SELECT = ("id,doi,title,publication_year,language,type,open_access,best_oa_location,locations,"
                   "authorships,cited_by_count,abstract_inverted_index,biblio,keywords,primary_location")


def openalex(http, query, o):
    flt = []
    if o.lang in ("ru", "en"):
        flt.append("language:%s" % o.lang)
    if o.from_year:
        flt.append("from_publication_date:%s-01-01" % o.from_year)
    if o.to_year:
        flt.append("to_publication_date:%s-12-31" % o.to_year)
    if o.oa_only:
        flt.append("is_oa:true")
    if o.types:
        flt.append("type:" + "|".join(o.types))
    params = {"search": query, "per-page": min(o.limit, 50), "select": OPENALEX_SELECT}
    if flt:
        params["filter"] = ",".join(flt)
    if o.sort == "citations":
        params["sort"] = "cited_by_count:desc"
    elif o.sort == "year":
        params["sort"] = "publication_year:desc"
    if _mailto():
        params["mailto"] = _mailto()
    r = http.get("https://api.openalex.org/works", params=params)
    if r.status_code == 429:
        raise SourceSkipped("OpenAlex: лимит запросов")
    r.raise_for_status()
    return [_oa_to_work(it) for it in r.json().get("results", [])]


def _oa_to_work(it):
    loc = it.get("primary_location") or {}
    b = it.get("biblio") or {}
    pages = "–".join(p for p in (b.get("first_page"), b.get("last_page")) if p) or None
    oa = it.get("open_access") or {}
    w = new_work("openalex", title=it.get("title"), year=it.get("publication_year"), type=it.get("type"),
                 language=it.get("language"), doi=it.get("doi"), cited_by=it.get("cited_by_count"),
                 authors=[(a.get("author") or {}).get("display_name") for a in it.get("authorships", [])
                          if (a.get("author") or {}).get("display_name")],
                 venue=(loc.get("source") or {}).get("display_name"),
                 abstract=reconstruct_abstract(it.get("abstract_inverted_index")),
                 keywords=[k.get("display_name") for k in it.get("keywords", []) if k.get("display_name")],
                 biblio={"volume": b.get("volume"), "issue": b.get("issue"), "pages": pages},
                 landing_url=loc.get("landing_page_url"), open=oa.get("is_oa"), ids={"openalex": it.get("id")})
    locs = list(it.get("locations") or [])
    if it.get("best_oa_location"):
        locs.insert(0, it["best_oa_location"])
    for l in locs:
        src = (l.get("source") or {}).get("display_name")
        if l.get("pdf_url"):
            w["candidates"].append(candidate(l["pdf_url"], "pdf", "openalex:%s" % src, l.get("license"), l.get("is_oa")))
        if l.get("landing_page_url"):
            w["candidates"].append(candidate(l["landing_page_url"], "landing", "openalex:%s" % src, l.get("license"),
                                             l.get("is_oa")))
    w["license"] = (it.get("best_oa_location") or {}).get("license")
    return w


def lookup_doi(http, doi):
    """Одна работа по DOI (OpenAlex)."""
    doi = norm_doi(doi)
    params = {"select": OPENALEX_SELECT}
    if _mailto():
        params["mailto"] = _mailto()
    r = http.get("https://api.openalex.org/works/https://doi.org/" + doi, params=params)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return _oa_to_work(r.json())


# --------------------------------------------------------------------------
# Crossref — метаданные по DOI; для русских журналов покрытие мало, зато надёжные библиографические данные
# --------------------------------------------------------------------------


# Типы из запроса пользователя (словарь OpenAlex) -> типы Crossref (у него свои названия)
CROSSREF_TYPES = {"article": "journal-article", "book": "book", "book-chapter": "book-chapter",
                  "dissertation": "dissertation", "preprint": "posted-content", "proceedings-article": "proceedings-article"}


def crossref(http, query, o):
    types = [CROSSREF_TYPES[t] for t in o.types if t in CROSSREF_TYPES]
    if o.types and not types:
        raise SourceSkipped("Crossref: нет соответствия типам %s" % ", ".join(o.types))
    flt = ["type:%s" % t for t in (types or ["journal-article"])]
    if o.from_year:
        flt.append("from-pub-date:%s" % o.from_year)
    if o.to_year:
        flt.append("until-pub-date:%s" % o.to_year)
    params = {"query.bibliographic": query, "rows": min(o.limit, 50), "filter": ",".join(flt)}
    if o.sort == "citations":
        params.update({"sort": "is-referenced-by-count", "order": "desc"})
    elif o.sort == "year":
        params.update({"sort": "published", "order": "desc"})
    if _mailto():
        params["mailto"] = _mailto()
    r = http.get("https://api.crossref.org/works", params=params)
    if r.status_code == 429:
        raise SourceSkipped("Crossref: лимит запросов")
    r.raise_for_status()
    out = []
    for it in r.json()["message"].get("items", []):
        issued = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        lic = next((l.get("URL") for l in it.get("license", []) if "creativecommons" in (l.get("URL") or "")), None)
        w = new_work("crossref", title=(it.get("title") or [None])[0], year=issued[0] if issued else None,
                     type=it.get("type"), language=it.get("language"), doi=it.get("DOI"),
                     cited_by=it.get("is-referenced-by-count"), venue=(it.get("container-title") or [None])[0],
                     authors=["%s %s" % (a.get("given", ""), a.get("family", "")) if a.get("given") else a.get("name", "")
                              for a in it.get("author", []) if a.get("family") or a.get("name")],
                     abstract=strip_tags(it.get("abstract")) or None, landing_url=it.get("URL"),
                     keywords=it.get("subject") or [], license=lic, open=True if lic else None,
                     biblio={"volume": it.get("volume"), "issue": it.get("issue"), "pages": it.get("page")})
        for l in it.get("link", []):
            if "pdf" in (l.get("content-type") or ""):
                w["candidates"].append(candidate(l["URL"], "pdf", "crossref", lic, True if lic else None))
        if it.get("URL"):
            w["candidates"].append(candidate(it["URL"], "landing", "crossref", lic, True if lic else None))
        out.append(w)
    return _filter(out, o)


# --------------------------------------------------------------------------
# DOAJ — журналы открытого доступа (все статьи открыты)
# --------------------------------------------------------------------------


def doaj(http, query, o):
    if o.types and "article" not in o.types:
        raise SourceSkipped("DOAJ: только статьи журналов")
    q = query
    if o.from_year or o.to_year:
        q = "(%s) AND bibjson.year:[%s TO %s]" % (query, o.from_year or 1900, o.to_year or 2100)
    r = http.get("https://doaj.org/api/search/articles/" + quote(q, safe=""), params={"pageSize": min(o.limit, 50)})
    if r.status_code == 429:
        raise SourceSkipped("DOAJ: лимит запросов")
    r.raise_for_status()
    out = []
    for it in r.json().get("results", []):
        b = it.get("bibjson") or {}
        j = b.get("journal") or {}
        doi = next((i["id"] for i in b.get("identifier", []) if i.get("type") == "doi"), None)
        pages = "–".join(p for p in (b.get("start_page"), b.get("end_page")) if p) or None
        langs = j.get("language") or []
        w = new_work("doaj", title=b.get("title"), year=_int(b.get("year")), type="article", doi=doi, open=True,
                     authors=[a.get("name") for a in b.get("author", []) if a.get("name")], venue=j.get("title"),
                     abstract=b.get("abstract"), keywords=b.get("keywords") or [],
                     language=(langs[0].lower() if len(langs) == 1 else None),
                     biblio={"volume": j.get("volume"), "issue": j.get("number"), "pages": pages})
        for l in b.get("link", []):
            if l.get("type") == "fulltext":
                kind = "pdf" if (l.get("content_type") or "").lower() == "pdf" else "landing"
                w["candidates"].append(candidate(l["url"], kind, "doaj", None, True))
                w["landing_url"] = w["landing_url"] or l["url"]
        out.append(w)
    return _filter(out, o)


# --------------------------------------------------------------------------
# OpenAIRE (Graph API) — открытые репозитории, в том числе российские вузовские
# --------------------------------------------------------------------------


def openaire(http, query, o):
    r = http.get("https://api.openaire.eu/graph/v1/researchProducts",
                 params={"search": query, "type": "publication", "pageSize": min(o.limit, 50)})
    if r.status_code == 429:
        raise SourceSkipped("OpenAIRE: лимит запросов")
    r.raise_for_status()
    out = []
    for it in r.json().get("results", []):
        pids = it.get("pids") or []
        doi = next((p.get("value") for p in pids if isinstance(p, dict) and p.get("scheme") == "doi"), None)
        authors = []
        for a in it.get("authors") or []:
            if isinstance(a, dict):
                authors.append(a.get("fullName") or a.get("name"))
            elif a:
                authors.append(str(a))
        best = it.get("bestAccessRight") or {}
        is_open = "open" in str(best.get("label") or "").lower() if best else None
        desc = it.get("descriptions") or []
        w = new_work("openaire", title=it.get("mainTitle"), year=_int(it.get("publicationDate")),
                     type=it.get("type"), doi=doi, language=(it.get("language") or {}).get("code"),
                     authors=[a for a in authors if a], venue=(it.get("container") or {}).get("name") or it.get("publisher"),
                     abstract=strip_tags(desc[0]) if desc else None, open=is_open if is_open else None,
                     keywords=[s["subject"]["value"] for s in it.get("subjects") or []
                               if (s.get("subject") or {}).get("scheme") == "keyword"],
                     ids={"openaire": it.get("id")})
        for inst in it.get("instances") or []:
            for url in inst.get("urls") or []:
                kind = "pdf" if url.lower().split("?")[0].endswith(".pdf") else "landing"
                w["candidates"].append(candidate(url, kind, "openaire", None, is_open or None))
                w["landing_url"] = w["landing_url"] or url
        out.append(w)
    return _filter(out, o)


# --------------------------------------------------------------------------
# Semantic Scholar и CORE — опциональные, нужен ключ в переменной окружения
# --------------------------------------------------------------------------


def semanticscholar(http, query, o):
    key = os.environ.get("S2_API_KEY")
    params = {"query": query, "limit": min(o.limit, 50),
              "fields": "title,year,authors,venue,abstract,externalIds,openAccessPdf,citationCount,publicationTypes"}
    if o.from_year or o.to_year:
        params["year"] = "%s-%s" % (o.from_year or "", o.to_year or "")
    r = http.get("https://api.semanticscholar.org/graph/v1/paper/search", params=params,
                 headers={"x-api-key": key} if key else None, retries=0)
    if r.status_code == 429:
        raise SourceSkipped("Semantic Scholar: лимит запросов (без ключа S2_API_KEY это частая ситуация)")
    r.raise_for_status()
    out = []
    for it in r.json().get("data", []):
        pdf = (it.get("openAccessPdf") or {}).get("url")
        w = new_work("semanticscholar", title=it.get("title"), year=it.get("year"), doi=(it.get("externalIds") or {}).get("DOI"),
                     authors=[a.get("name") for a in it.get("authors", []) if a.get("name")], venue=it.get("venue") or None,
                     abstract=it.get("abstract"), cited_by=it.get("citationCount"), open=True if pdf else None,
                     type=(it.get("publicationTypes") or [None])[0])
        if pdf:
            w["candidates"].append(candidate(pdf, "pdf", "semanticscholar", None, True))
        out.append(w)
    return _filter(out, o)


def core(http, query, o):
    key = os.environ.get("CORE_API_KEY")
    if not key:
        raise SourceSkipped("CORE: нужен ключ в переменной окружения CORE_API_KEY (бесплатная регистрация на core.ac.uk)")
    r = http.get("https://api.core.ac.uk/v3/search/works", params={"q": query, "limit": min(o.limit, 50)},
                 headers={"Authorization": "Bearer " + key})
    if r.status_code == 429:
        raise SourceSkipped("CORE: лимит запросов")
    r.raise_for_status()
    out = []
    for it in r.json().get("results", []):
        w = new_work("core", title=it.get("title"), year=_int(it.get("yearPublished")), doi=it.get("doi"),
                     language=(it.get("language") or {}).get("code"), abstract=it.get("abstract"),
                     authors=[a.get("name") for a in it.get("authors", []) if a.get("name")], open=True if it.get("downloadUrl") else None)
        if it.get("downloadUrl"):
            w["candidates"].append(candidate(it["downloadUrl"], "pdf", "core", None, True))
        out.append(w)
    return _filter(out, o)


class Source:
    def __init__(self, name, fn, note, default=True, key_env=None, verified=True):
        self.name, self.fn, self.note, self.default, self.key_env, self.verified = name, fn, note, default, key_env, verified


REGISTRY = {s.name: s for s in [
    Source("openalex", openalex, "основной: язык, год, тип, открытый доступ, ссылки на полные тексты (в том числе КиберЛенинка и репозитории)"),
    Source("crossref", crossref, "метаданные по DOI; для русских журналов покрытие небольшое"),
    Source("doaj", doaj, "журналы открытого доступа, в том числе русские; запрос в синтаксисе Lucene"),
    Source("openaire", openaire, "открытые репозитории (диссертации, вузовские архивы)"),
    Source("semanticscholar", semanticscholar, "англоязычная литература; без ключа частые 429", default=False, key_env="S2_API_KEY"),
    Source("core", core, "тексты из репозиториев открытого доступа; нужен бесплатный ключ", default=False, key_env="CORE_API_KEY",
           verified=False),
]}
