# -*- coding: utf-8 -*-
"""Загрузка полных текстов в открытом доступе, извлечение текста и список «скачать вручную».

Правила: качаем только то, что отдано в открытом доступе и не запрещено robots.txt сайта; eLIBRARY и всё, что
требует подписки или запрещает роботов, в загрузку не идёт, а попадает в список для ручного скачивания пользователем.
"""

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .model import access_status, format_citation

MAX_BYTES = 40 * 1024 * 1024
MIN_ARTICLE_TEXT = 1500

_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))

REASONS = {
    "elibrary": "Статья на eLIBRARY: скачивание требует авторизации и подписки, автоматизация запрещена правилами сервиса. "
                "Скачайте вручную под своей подпиской (найдите по DOI или названию).",
    "closed": "Нет открытого доступа: скачайте вручную под своей подпиской или через библиотеку (ссылки ниже).",
    "robots": "Сайт запрещает автоматическую загрузку (robots.txt): откройте ссылку и скачайте вручную.",
    "no_link": "Ссылки на полный текст в источниках нет: найдите по DOI или названию (ссылки ниже).",
    "not_pdf": "По ссылке не PDF и не страница с текстом статьи: откройте ссылку вручную.",
    "failed": "Не удалось скачать (ошибка сети или ответа сервера): откройте ссылку вручную или повторите позже.",
    "too_large": "Файл слишком большой для автоматической загрузки: скачайте вручную.",
}


def slugify(text, limit=40):
    s = "".join(_TR.get(ch, ch) for ch in (text or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:limit].strip("-") or "work"


def base_name(w):
    author = slugify((w["authors"][0] if w.get("authors") else "anon").split()[-1], 20)
    ident = (w.get("doi") or w["title"] or "")
    return "%s_%s_%s_%s" % (w.get("year") or "nd", author, slugify(w["title"], 36),
                            hashlib.sha1(ident.encode("utf-8")).hexdigest()[:6])


class _Page(HTMLParser):
    """Достаёт из HTML ссылку на PDF (citation_pdf_url) и текст из блока itemprop=articleBody."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pdf = None
        self.depth = 0
        self.body = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("name") == "citation_pdf_url":
            self.pdf = a.get("content")
        if self.depth:
            self.depth += 1
            if tag in ("br", "p", "h1", "h2", "h3", "li"):
                self.body.append("\n")
        elif a.get("itemprop") == "articleBody":
            self.depth = 1

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1
            if tag in ("p", "h1", "h2", "h3", "li"):
                self.body.append("\n")

    def handle_data(self, data):
        if self.depth:
            self.body.append(data)

    @property
    def text(self):
        text = "".join(self.body).replace("﻿", "").replace("\xa0", " ")
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def extract_pdf_text(path):
    exe = shutil.which("pdftotext")
    if exe:
        try:
            p = subprocess.run([exe, "-enc", "UTF-8", "-nopgbrk", str(path), "-"], capture_output=True, timeout=120)
            if p.returncode == 0:
                return p.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import pypdf  # необязательная зависимость
        return "\n".join((pg.extract_text() or "") for pg in pypdf.PdfReader(str(path)).pages)
    except Exception:
        return None


def _download_pdf(http, url, dest):
    r = http.get(url, stream=True)
    try:
        if r.status_code != 200:
            return "failed", "HTTP %s" % r.status_code
        ctype = (r.headers.get("Content-Type") or "").lower()
        it = r.iter_content(65536)
        first = next(it, b"")
        if first[:5] == b"%PDF-":
            size, h = 0, hashlib.sha256()
            with open(dest, "wb") as f:
                for chunk in _chain(first, it):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        f.close()
                        Path(dest).unlink(missing_ok=True)
                        return "too_large", None
                    h.update(chunk)
                    f.write(chunk)
            return "pdf", {"bytes": size, "sha256": h.hexdigest()}
        if "html" in ctype or first.lstrip()[:1] == b"<":
            body = first + b"".join(_take(it, 3_000_000))
            return "html", body.decode(r.encoding or "utf-8", "replace")
        return "not_pdf", None
    finally:
        r.close()


def _chain(first, it):
    yield first
    yield from it


def _take(it, limit):
    total = 0
    for chunk in it:
        total += len(chunk)
        yield chunk
        if total >= limit:
            break


def fetch_work(http, w, out_dir, want_text=True):
    """Скачать работу. Возвращает ('downloaded', info) или ('manual', info)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = base_name(w)
    reasons = []
    tried = set()
    page_text = None
    queue = sorted([c for c in w["candidates"] if c.get("open") is not False],
                   key=lambda c: 0 if c["kind"] == "pdf" else 1)
    while queue:
        c = queue.pop(0)
        url = c["url"]
        if url in tried:
            continue
        tried.add(url)
        if "elibrary.ru" in c["host"]:
            reasons.append("elibrary")
            continue
        if not http.robots_allowed(url):
            reasons.append("robots")
            continue
        kind, payload = _download_pdf(http, url, out_dir / (name + ".pdf"))
        if kind == "pdf":
            info = {"path": str(out_dir / (name + ".pdf")), "format": "pdf", "source_url": url, **payload}
            if want_text:
                text = extract_pdf_text(info["path"])
                if text and len(text.strip()) > 200:
                    (out_dir / (name + ".txt")).write_text(text, encoding="utf-8")
                    info["text_path"] = str(out_dir / (name + ".txt"))
                else:
                    info["text_path"] = None
                    info["text_note"] = "текст не извлечён (нет pdftotext/pypdf или PDF без текстового слоя)"
            return "downloaded", _finish(w, info, out_dir, name)
        if kind == "html":
            page = _Page()
            page.feed(payload)
            has_text = len(page.text) >= MIN_ARTICLE_TEXT
            pdf = urljoin(url, page.pdf) if page.pdf else None
            if pdf and pdf not in tried and http.robots_allowed(pdf):
                if has_text and not page_text:
                    page_text = (url, page.text)  # запасной путь, если PDF не скачается
                queue.insert(0, {"url": pdf, "kind": "pdf", "host": urlparse(pdf).netloc.lower(), "open": True})
            elif has_text:
                return "downloaded", _save_page_text(w, url, page.text, out_dir, name)
            else:
                reasons.append("robots" if pdf and pdf not in tried else "not_pdf")
            continue
        reasons.append(kind if kind in REASONS else "failed")
    if page_text:
        return "downloaded", _save_page_text(w, page_text[0], page_text[1], out_dir, name)
    if not w["candidates"]:
        reason = "no_link"
    elif w.get("open") is False and not reasons:
        reason = "closed"
    else:
        reason = next((r for r in ("elibrary", "robots", "too_large", "failed", "not_pdf") if r in reasons),
                      "closed" if access_status(w) == "closed" else "no_link")
    return "manual", manual_entry(w, reason)


def _save_page_text(w, url, text, out_dir, name):
    """Текст статьи, извлечённый со страницы сайта (когда PDF недоступен или запрещён robots.txt)."""
    header = "%s\n\nИсточник: %s\nИзвлечено: %s (текст со страницы сайта, открытый доступ)\n\n" % (
        w["title"], url, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    path = Path(out_dir) / (name + ".txt")
    path.write_text(header + text, encoding="utf-8")
    info = {"path": str(path), "format": "html-text", "source_url": url, "bytes": len(text.encode("utf-8")),
            "text_path": str(path)}
    return _finish(w, info, out_dir, name)


def _finish(w, info, out_dir, name):
    meta = {"title": w["title"], "authors": w["authors"], "year": w["year"], "venue": w["venue"], "doi": w["doi"],
            "citation": format_citation(w), "language": w["language"], "license": w["license"],
            "sources": w["sources"], "retrieved": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "file": Path(info["path"]).name, "format": info["format"], "source_url": info["source_url"],
            "sha256": info.get("sha256"), "bytes": info.get("bytes")}
    mp = Path(out_dir) / (name + ".meta.json")
    mp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    info["meta_path"] = str(mp)
    info["citation"] = meta["citation"]
    return info


def manual_entry(w, reason):
    links = []
    if w.get("doi"):
        links.append("https://doi.org/" + w["doi"])
    for c in w["candidates"]:
        if c["url"] not in links:
            links.append(c["url"])
    if w.get("landing_url") and w["landing_url"] not in links:
        links.append(w["landing_url"])
    return {"reason": reason, "message": REASONS[reason], "title": w["title"], "citation": format_citation(w),
            "doi": w.get("doi"), "links": links[:6],
            "elibrary": any("elibrary.ru" in c["host"] for c in w["candidates"]) or reason == "elibrary"}


def manual_markdown(entries, out_dir):
    """Список для пользователя: что и где скачать вручную."""
    lines = ["# Скачать вручную", "",
             "Эти материалы найдены, но автоматически загрузить их нельзя (подписка, запрет в robots.txt или нет открытой "
             "ссылки). Скачайте их под своей подпиской и положите PDF в рабочую папку; затем их можно подключить командой "
             "`ingest` инструмента science-search.", ""]
    for i, e in enumerate(entries, 1):
        lines += ["%d. %s" % (i, e["citation"]), "   - Почему: %s" % e["message"]]
        for l in e["links"]:
            lines.append("   - %s" % l)
        lines.append("")
    p = Path(out_dir) / "manual-downloads.md"
    p.write_text("\n".join(lines).replace("робots", "robots"), encoding="utf-8")
    return str(p)


def refs_suggestion(w, info):
    return {"section": "Научные материалы", "name": format_citation(w),
            "description": (w.get("abstract") or "")[:300] or None, "file": info.get("path"),
            "source": "научный источник (%s), вторичный источник, открытый доступ" % ", ".join(w["sources"]),
            "note": "Сохранять в docs/refs/science только после согласия пользователя (AGENTS.md)."}


def ingest_file(src, out_dir, w, want_text=True):
    """Подключить PDF, скачанный пользователем вручную: копия в рабочую папку, метаданные, извлечённый текст."""
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = base_name(w)
    dest = out_dir / (name + src.suffix.lower())
    shutil.copy2(src, dest)
    info = {"path": str(dest), "format": src.suffix.lower().lstrip("."), "source_url": "файл пользователя: " + src.name,
            "bytes": dest.stat().st_size, "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()}
    if want_text and src.suffix.lower() == ".pdf":
        text = extract_pdf_text(dest)
        if text and len(text.strip()) > 200:
            (out_dir / (name + ".txt")).write_text(text, encoding="utf-8")
            info["text_path"] = str(out_dir / (name + ".txt"))
        else:
            info["text_path"] = None
            info["text_note"] = "текст не извлечён (нет pdftotext/pypdf или PDF без текстового слоя)"
    return _finish(w, info, out_dir, name)
