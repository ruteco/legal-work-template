# -*- coding: utf-8 -*-
"""Единая модель работы (статьи, книги), слияние результатов разных источников, ранжирование, цитирование."""

import html as htmllib
import re
from urllib.parse import urlparse


def norm_doi(doi):
    if not doi:
        return None
    d = str(doi).strip().lower()
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d)
    return d or None


def norm_title(title):
    return re.sub(r"[^0-9a-zа-яё]+", " ", (title or "").lower()).strip()


def strip_tags(s):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def guess_lang(text):
    """Грубое определение языка по алфавиту: ru, если кириллица преобладает среди букв. Метки языка в
    источниках ненадёжны, поэтому проверяем и сами."""
    cyr = len(re.findall(r"[а-яё]", (text or "")[:400], re.I))
    lat = len(re.findall(r"[a-z]", (text or "")[:400], re.I))
    if cyr + lat < 5:
        return None
    return "ru" if cyr >= lat else "en"


def reconstruct_abstract(inv):
    """OpenAlex отдаёт аннотацию как {слово: [позиции]}."""
    if not inv:
        return None
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos)) or None


def host_of(url):
    return urlparse(url or "").netloc.lower()


def new_work(source, **kw):
    w = {"title": None, "authors": [], "year": None, "venue": None, "type": None, "language": None, "doi": None,
         "abstract": None, "keywords": [], "cited_by": None, "biblio": {}, "landing_url": None, "open": None,
         "license": None, "candidates": [], "sources": [source], "ids": {}}
    w.update(kw)
    w["doi"] = norm_doi(w["doi"])
    return w


def candidate(url, kind, source, license=None, open_=None):
    return {"url": url, "kind": kind, "host": host_of(url), "source": source, "license": license, "open": open_}


def work_key(w):
    return w["doi"] or ("t:%s:%s" % (norm_title(w["title"]), w["year"] or ""))


def merge(a, b):
    """Слить запись b в a: пустые поля берём у b, доступ и кандидаты объединяем."""
    for k in ("title", "year", "venue", "type", "language", "abstract", "cited_by", "landing_url", "license", "doi"):
        if not a.get(k) and b.get(k):
            a[k] = b[k]
    if len(b.get("authors") or []) > len(a.get("authors") or []):
        a["authors"] = b["authors"]
    a["keywords"] = sorted(set(a.get("keywords") or []) | set(b.get("keywords") or []))
    for k, v in (b.get("biblio") or {}).items():
        a["biblio"].setdefault(k, v)
    if b.get("open") is True or a.get("open") is None:
        a["open"] = b.get("open") if b.get("open") is not None else a.get("open")
    seen = {c["url"] for c in a["candidates"]}
    a["candidates"] += [c for c in b.get("candidates") or [] if c["url"] not in seen]
    a["sources"] = sorted(set(a["sources"]) | set(b["sources"]))
    a["ids"].update(b.get("ids") or {})
    return a


def fuse(result_lists, k=60):
    """Reciprocal rank fusion: работа, высоко стоящая в нескольких источниках и запросах, поднимается."""
    merged, score = {}, {}
    for works in result_lists:
        for rank, w in enumerate(works):
            key = work_key(w)
            if key in merged:
                merge(merged[key], w)
            else:
                merged[key] = w
            score[key] = score.get(key, 0.0) + 1.0 / (k + rank + 1)
    for key, w in merged.items():
        w["score"] = round(score[key], 5)
    return sorted(merged.values(), key=lambda w: -w["score"])


def access_status(w):
    """open — есть признак открытого доступа и/или ссылка на полный текст; closed — источники сообщают, что доступа нет."""
    if w.get("open") is True or any(c["kind"] == "pdf" and c.get("open") is not False for c in w["candidates"]):
        return "open"
    if w.get("open") is False:
        return "closed"
    return "unknown"


def format_citation(w):
    """Библиографическая запись в духе ГОСТ Р 7.0.5-2008 (для проверки по первоисточнику)."""
    authors = w.get("authors") or []
    a = ", ".join(authors[:3]) + (" и др." if len(authors) > 3 else "")
    s = ("%s " % a if a else "") + (w.get("title") or "").rstrip(".")
    if w.get("venue"):
        s += " // " + w["venue"]
    tail = []
    if w.get("year"):
        tail.append(str(w["year"]))
    b = w.get("biblio") or {}
    if b.get("volume"):
        tail.append("Т. %s" % b["volume"])
    if b.get("issue"):
        tail.append("№ %s" % b["issue"])
    if b.get("pages"):
        tail.append("С. %s" % b["pages"])
    if tail:
        s += ". " + ". ".join(tail)
    if w.get("doi"):
        s += ". DOI: %s" % w["doi"]
    return s.strip()


def public_view(w, n, abstract_chars=500):
    """Компактная запись для вывода пользователю и агенту."""
    return {"n": n, "title": w["title"], "authors": w["authors"][:6], "year": w["year"], "venue": w["venue"],
            "type": w["type"], "language": w["language"] or guess_lang(w["title"]), "doi": w["doi"],
            "cited_by": w["cited_by"], "access": access_status(w), "sources": w["sources"],
            "abstract": (w["abstract"][:abstract_chars] + "…") if w.get("abstract") and len(w["abstract"]) > abstract_chars
            else w.get("abstract"), "landing_url": w["landing_url"], "citation": format_citation(w)}
