#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
split_document.py — разбор исходного документа (закон, кодекс, НПА) в каталог markdown-файлов.

Идея: документ нельзя целиком загружать в контекст (НК РФ ч.2 — ~1,8 млн токенов). Скрипт режет его
по структуре (раздел / глава / статья) на файлы разумного размера, кладёт рядом индекс и паспорт,
а команда `read` печатает только нужную статью или главу.

Команды:
  split   ВХОД.fb2 --out-dir КАТАЛОГ [--max-chars 30000] [--base LAW --n 541106] [--force]
  outline КАТАЛОГ [--depth 2]
  read    КАТАЛОГ "Статья 45" | "Глава 21" | --id Par123

Читатель: FB2 КонсультантПлюс (оглавление-якоря #ParNN, плоские абзацы). Другие источники — новые
функции-читатели, возвращающие ту же модель (Doc/Node), остальной код не меняется.
"""

import argparse
import base64
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FB = "{http://www.gribuser.ru/xml/fictionbook/2.0}"
XL = "{http://www.w3.org/1999/xlink}"
AMEND_MARK = "Список изменяющих документов"
NOTE_PREFIX = "КонсультантПлюс: примечание"
FOOTER_PREFIX = "Документ предоставлен"
IMG_RE = re.compile(r"\{\{IMG:([^}]+)\}\}")
CHARS_PER_TOKEN = 3  # грубая оценка для русского текста

# --------------------------------------------------------------------------
# Утилиты: транслитерация, метки структуры
# --------------------------------------------------------------------------

_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r",
                "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))
KINDS = {"часть": "chast", "раздел": "razdel", "подраздел": "podrazdel", "глава": "glava",
         "параграф": "par", "статья": "st", "приложение": "pril"}
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def slugify(text, limit=40):
    s = "".join(_TR.get(ch, ch) for ch in text.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:limit].strip("-") or "part"


def roman_to_int(s):
    total, prev = 0, 0
    for ch in reversed(s.upper()):
        v = _ROMAN[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def label_of(title):
    """(вид, номер, slug) по заголовку вида «Статья 45.1. …», «Глава II. …»; иначе вид None."""
    m = re.match(r"^(Часть|Раздел|Подраздел|Глава|Параграф|§|Статья|Приложение)\s*([IVXLCDM]+(?:\.\d+)*|\d+(?:[.\-]\d+)*)\b",
                 title.strip(), re.I)
    if not m:
        return None, None, slugify(title, 30)
    kind = "параграф" if m.group(1) == "§" else m.group(1).lower()
    num = m.group(2)
    rm = re.fullmatch(r"([IVXLCDM]+)((?:\.\d+)*)", num.upper())
    if rm:  # римская нумерация («VIII.1») -> «8.1»
        num = str(roman_to_int(rm.group(1))) + rm.group(2)
    return kind, num, "%s-%s" % (KINDS[kind], num.replace(".", "-"))


def norm(text):
    return re.sub(r"[ \t]+", " ", (text or "").replace("\xa0", " ")).strip()


# --------------------------------------------------------------------------
# Модель документа
# --------------------------------------------------------------------------


RANK = {"часть": 1, "раздел": 2, "подраздел": 3, "глава": 4, "параграф": 5, "статья": 6, "приложение": 2}


class Node:
    def __init__(self, level, title, anchor):
        # level уточняется при построении дерева: маркеры «◦» в оглавлении ненадёжны
        # (в НК главы и статьи стоят на одном уровне), иерархию задаёт вид заголовка.
        self.level, self.title, self.anchor = level, title, anchor
        self.kind, self.num, self.slug = label_of(title)
        self.rank = 0
        self.lines, self.children, self.parent = [], [], None
        self._sub = None

    def own_chars(self):
        return len(self.title) + 2 + sum(len(x) + 1 for x in self.lines)

    def subtree_chars(self):
        if self._sub is None:
            self._sub = self.own_chars() + sum(c.subtree_chars() for c in self.children)
        return self._sub

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


class Doc:
    def __init__(self):
        self.meta, self.preamble, self.roots = {}, [], []
        self.amendments, self.links, self.binaries = [], [], {}
        self.toc_count = 0
        self.notes = 0


# --------------------------------------------------------------------------
# Читатель FB2 КонсультантПлюс
# --------------------------------------------------------------------------


def inline_text(el):
    parts = []

    def walk(e):
        if e.text:
            parts.append(e.text)
        for c in e:
            if c.tag == FB + "image":
                parts.append("{{IMG:%s}}" % (c.get(XL + "href") or "").lstrip("#"))
            else:
                walk(c)
            if c.tail:
                parts.append(c.tail)

    walk(el)
    return "".join(parts)


def ptext(p):
    return "\n".join(norm(x) for x in inline_text(p).split("\n")).strip()


def links_of(el):
    out = []
    for a in el.iter(FB + "a"):
        href = a.get(XL + "href") or ""
        if href.startswith("#"):
            continue
        q = dict(parse_qsl(urlparse(href.replace("&amp;", "&")).query))
        if q.get("req") == "doc" and q.get("n"):
            out.append({"text": norm("".join(a.itertext())), "base": q.get("base"),
                        "n": int(q["n"]) if q["n"].isdigit() else q["n"], "dst": q.get("dst")})
    return out


def parse_toc(children):
    start = next((i for i, e in enumerate(children) if e.tag == FB + "p" and ptext(e) == "Оглавление"), None)
    toc = {}
    if start is None:
        return toc, 0
    i = start + 1
    while i < len(children):
        e = children[i]
        if e.tag == FB + "empty-line" or (e.tag == FB + "p" and not ptext(e)):
            i += 1
            continue
        a = e.find(FB + "a") if e.tag == FB + "p" else None
        href = a.get(XL + "href") if a is not None else ""
        if not (href and href.startswith("#")):
            break
        raw = ptext(e)
        level = 1 + len(raw) - len(raw.lstrip("◦"))
        toc[href[1:]] = (level, raw.lstrip("◦").strip())
        i += 1
    return toc, i


def table_rows(tbl):
    rows = []
    for tr in tbl.iter(FB + "tr"):
        cells = [norm(" ".join(ptext(p) for p in td.iter(FB + "p")) or "".join(td.itertext()))
                 for td in tr.findall(FB + "td")]
        if any(cells):
            rows.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
    return rows


def read_fb2(raw):
    root = ET.fromstring(raw)
    doc = Doc()
    ti = root.find(FB + "description/" + FB + "title-info")
    di = root.find(FB + "description/" + FB + "document-info")
    title = norm(ti.findtext(FB + "book-title")) if ti is not None else ""
    ed = re.search(r"\(ред\. от (\d\d\.\d\d\.\d{4})\)", title)
    doc.meta = {"title": title.replace("\n", " "), "edition": ed.group(1) if ed else None,
                "exported": norm(di.findtext(FB + "date")) if di is not None else None}
    doc.binaries = {b.get("id"): (b.get("content-type"), b.text or "") for b in root.findall(FB + "binary")}
    body = root.find(FB + "body")
    section = body.find(FB + "section")
    children = list(section) if section is not None else list(body)
    toc, first = parse_toc(children)
    doc.toc_count = len(toc)

    cur = doc.preamble
    stack = []

    def add(line):
        if line == "":
            if cur and cur[-1] != "":
                cur.append("")
        else:
            cur.append(line)

    owner = "preamble"
    for e in children[first:]:
        if e.tag == FB + "empty-line":
            add("")
            continue
        if e.tag == FB + "image":
            add("{{IMG:%s}}" % (e.get(XL + "href") or "").lstrip("#"))
            continue
        if e.tag == FB + "table":
            full = norm("".join(e.itertext()))
            if AMEND_MARK in full[:200]:
                doc.amendments += ["%s N %s" % x for x in re.findall(r"от (\d\d\.\d\d\.\d{4}) N ([\w.\-/]+)", full)]
                doc.links += [dict(l, **{"in": "amendments"}) for l in links_of(e)]
                continue
            if full.startswith(NOTE_PREFIX):
                doc.notes += 1
                add("")
                add("> [КП] " + re.sub(r"^КонсультантПлюс: примечание\.?\s*", "", full))
                add("")
                continue
            rows = table_rows(e)
            if rows:
                add("")
                for ln in [rows[0], "|" + "---|" * (rows[0].count("|") - 1)] + rows[1:]:
                    add(ln)
                add("")
            doc.links += [dict(l, **{"in": owner}) for l in links_of(e)]
            continue
        if e.tag != FB + "p":
            continue
        text = ptext(e)
        if not text:
            add("")
            continue
        if re.fullmatch(r"-{10,}", text) or text.startswith(FOOTER_PREFIX):
            continue
        pid = e.get("id")
        if pid in toc:
            level, ttl = toc[pid]
            node = Node(level, ttl, pid)
            node.rank = RANK.get(node.kind) or ((stack[-1].rank + 1) if stack else 1)
            while stack and stack[-1].rank >= node.rank:
                stack.pop()
            if stack:
                node.parent = stack[-1]
                node.level = stack[-1].level + 1
                stack[-1].children.append(node)
            else:
                node.level = 1
                doc.roots.append(node)
            stack.append(node)
            cur = node.lines
            owner = pid
            continue
        add(text)
        doc.links += [dict(l, **{"in": owner}) for l in links_of(e)]
    for n in [None] + [x for r in doc.roots for x in r.walk()]:
        lines = doc.preamble if n is None else n.lines
        while lines and lines[-1] == "":
            lines.pop()
    return doc


# --------------------------------------------------------------------------
# Планирование файлов и запись
# --------------------------------------------------------------------------


class Spec:
    """Один файл: путь, узлы, режим «только вступление узла» (для узла-каталога)."""

    def __init__(self, folder, name, nodes, intro_only=False, preamble=False):
        self.folder, self.name, self.nodes = folder, name, nodes
        self.intro_only, self.preamble = intro_only, preamble

    @property
    def rel(self):
        return (self.folder / self.name) if str(self.folder) != "." else Path(self.name)


def range_slug(nodes):
    a, b = nodes[0].slug, nodes[-1].slug
    return a if a == b or len(nodes) == 1 else "%s--%s" % (a, b)


def plan(nodes, folder, max_chars, state):
    specs, pack, size = [], [], 0

    def flush():
        nonlocal pack, size
        if pack:
            state["i"] += 1
            specs.append(Spec(folder, "%02d_%s.md" % (state["i"], range_slug(pack)[:70]), pack))
            pack, size = [], 0

    for n in nodes:
        sz = n.subtree_chars()
        if sz > max_chars and n.children:
            flush()
            state["i"] += 1
            sub = folder / ("%02d_%s" % (state["i"], n.slug[:50]))
            specs.append(Spec(sub, "00_intro.md", [n], intro_only=True))
            specs += plan(n.children, sub, max_chars, {"i": 0})
        else:
            if pack and size + sz > max_chars:
                flush()
            pack.append(n)
            size += sz
    flush()
    return specs


def render(node, lines, pos, with_children=True):
    if lines and lines[-1] != "":
        lines.append("")
    lines.append("#" * min(node.level + 1, 6) + " " + node.title)
    start = len(lines)
    lines.extend(node.lines)
    if with_children:
        for c in node.children:
            render(c, lines, pos)
    pos[node] = (start, len(lines))


def short(s, n=60):
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def write_split(doc, out, max_chars, base, n, name):
    out.mkdir(parents=True, exist_ok=True)
    src = "consultant %s/%s" % (base, n) if base and n else "consultant"
    ed = doc.meta.get("edition")
    head_common = "%s | %s" % (src, ("ред. от " + ed) if ed else "редакция не указана")
    specs = [Spec(Path("."), "00_preambula.md", [], preamble=True)]  # реквизиты и вводная часть (всегда)
    specs += plan(doc.roots, Path("."), max_chars, {"i": 0})

    img_names, files, pos_all = {}, [], {}
    in_files = 0
    for sp in specs:
        target = out / sp.rel
        target.parent.mkdir(parents=True, exist_ok=True)
        depth = len(sp.rel.parts) - 1
        lines = []
        pos = {}
        if sp.preamble:
            lines.append("# " + doc.meta["title"])
            lines.extend(doc.preamble)
            desc = "преамбула"
        else:
            for nd in sp.nodes:
                render(nd, lines, pos, with_children=not sp.intro_only)
            desc = short(sp.nodes[0].title, 50) if len(sp.nodes) == 1 else \
                "%s — %s" % (short(sp.nodes[0].title, 40), short(sp.nodes[-1].title, 40))
            if sp.intro_only:
                desc += " (вступление; части главы — в подкаталоге)"
        header = "<!-- %s | %s -->" % (head_common, desc)
        text_lines = [header] + lines
        in_files += sum(1 for x in lines if x.strip())
        body = "\n".join(text_lines) + "\n"
        rel_assets = "../" * depth + "assets/"

        def sub_img(m):
            iid = m.group(1)
            if iid not in doc.binaries:
                return ""
            if iid not in img_names:  # короткие имена экономят токены: img-001.png
                img_names[iid] = "img-%03d%s" % (len(img_names) + 1, image_ext(doc.binaries[iid][0]))
            return "![изображение](%s%s)" % (rel_assets, img_names[iid])

        body = IMG_RE.sub(sub_img, body)
        target.write_text(body, encoding="utf-8")
        chars = len(body)
        files.append({"path": sp.rel.as_posix(), "what": desc, "chars": chars,
                      "tokens_est": round(chars / CHARS_PER_TOKEN)})
        for nd, (s0, e0) in pos.items():
            pos_all[nd] = (sp.rel.as_posix(), s0 + 1, e0 + 1, sp.intro_only)  # +1: строка заголовка-комментария

    # изображения
    images = []
    if img_names:
        (out / "assets").mkdir(exist_ok=True)
        for iid, fn in img_names.items():
            ctype, data = doc.binaries[iid]
            (out / "assets" / fn).write_bytes(base64.b64decode(data))
            images.append({"id": iid, "file": "assets/" + fn, "content_type": ctype})

    # узлы
    all_nodes = [x for r in doc.roots for x in r.walk()]
    nodes = []
    for nd in all_nodes:
        f, s0, e0, intro = pos_all.get(nd, (None, None, None, False))
        nodes.append({"id": nd.anchor, "level": nd.level, "kind": nd.kind, "num": nd.num, "title": nd.title,
                      "file": f, "line_start": s0, "line_end": e0,
                      "chars": nd.subtree_chars(), "split_into_files": bool(intro),
                      "parent": nd.parent.anchor if nd.parent else None})

    # проверки целостности
    tree_lines = 0
    tree_lines += sum(1 for x in doc.preamble if x.strip()) + 1  # + заголовок «# название»
    for nd in all_nodes:
        tree_lines += 1 + sum(1 for x in nd.lines if x.strip())
    missing = [nd.anchor for nd in all_nodes if nd not in pos_all]
    oversize = [f for f in files if f["chars"] > max_chars * 1.15 and not f["what"].endswith("подкаталоге)")]
    validation = {
        "toc_entries": doc.toc_count, "headings_in_text": len(all_nodes),
        "toc_equals_headings": doc.toc_count == len(all_nodes),
        "nodes_not_written": missing,
        "nonempty_lines_in_tree": tree_lines, "nonempty_lines_in_files": in_files,
        "lines_match": tree_lines == in_files,
        "oversize_files": [f["path"] for f in oversize],
    }
    validation["ok"] = (validation["toc_equals_headings"] and validation["lines_match"] and not missing)

    stats = {"files": len(files), "chars": sum(f["chars"] for f in files),
             "tokens_est": round(sum(f["chars"] for f in files) / CHARS_PER_TOKEN),
             "articles": sum(1 for nd in all_nodes if nd.kind == "статья"),
             "headings": len(all_nodes), "images": len(images), "editorial_notes": doc.notes,
             "amendments": len(doc.amendments), "links": len(doc.links)}
    index = {"meta": dict(doc.meta, source="consultant", base=base, n=n, name=name,
                          split_date=datetime.now().strftime("%Y-%m-%d"), max_chars=max_chars),
             "stats": stats, "validation": validation, "files": files, "nodes": nodes, "images": images}
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "links.json").write_text(json.dumps(doc.links, ensure_ascii=False), encoding="utf-8")
    (out / "amendments.json").write_text(json.dumps(doc.amendments, ensure_ascii=False), encoding="utf-8")
    (out / "README.md").write_text(readme(index), encoding="utf-8")
    return index


def image_ext(ctype):
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/svg+xml": ".svg"}.get(ctype or "", "")


def readme(ix):
    m, s, v = ix["meta"], ix["stats"], ix["validation"]
    L = ["# " + m["title"], "",
         "- Редакция: %s (выгружено %s)" % (("ред. от " + m["edition"]) if m.get("edition") else "не указана", m.get("exported")),
         "- Источник: %s%s" % (m["source"], (" %s/%s" % (m["base"], m["n"])) if m.get("base") else ""),
         "- Объём: %d тыс. симв. (~%d тыс. токенов), файлов %d, статей %d, заголовков %d, изображений %d"
         % (s["chars"] // 1000, s["tokens_est"] // 1000, s["files"], s["articles"], s["headings"], s["images"]),
         "- Целостность: %s (оглавление %d = заголовков %d; строки дерева = строки файлов: %s)"
         % ("OK" if v["ok"] else "ЕСТЬ ПРОБЛЕМЫ", v["toc_entries"], v["headings_in_text"], "да" if v["lines_match"] else "нет"),
         "", "## Как читать", "",
         "1. Целиком документ не загружать. Найти часть: таблица ниже или `outline` (структура с размерами).",
         "2. Прочитать нужное: `python .skills/legal-doc-splitter/scripts/split_document.py read <каталог> \"Статья 45\"`",
         "   (печатает только эту статью/главу) или открыть файл из таблицы.",
         "3. `index.json` — все заголовки с файлом и строками; `links.json` — ссылки на другие документы",
         "   (base, n, dst); `amendments.json` — изменяющие акты; примечания КонсультантПлюс в тексте помечены `> [КП]`.",
         "", "## Файлы", "", "| Файл | Содержимое | Симв. | ~Токенов |", "|---|---|---|---|"]
    for f in ix["files"]:
        L.append("| %s | %s | %d | %d |" % (f["path"], f["what"].replace("|", "/"), f["chars"], f["tokens_est"]))
    if v["oversize_files"]:
        L += ["", "## Крупные статьи", "",
              "Эти файлы — одна статья, которую нельзя разделить по структуре. Не открывайте их целиком, читайте по пункту:",
              "`read <каталог> \"Статья N\" --point 4`.", ""]
        L += ["- " + p for p in v["oversize_files"]]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# Команды
# --------------------------------------------------------------------------


def cmd_split(a):
    src = Path(a.input)
    out = Path(a.out_dir)
    if out.exists() and any(out.iterdir()):
        if not a.force:
            sys.exit("Каталог не пуст: %s (--force перезапишет только результат прежнего разбиения)" % out)
        if not (out / "index.json").exists():
            sys.exit("В %s нет index.json: это не результат разбиения, удалять его содержимое я не буду." % out)
        shutil.rmtree(out)
    doc = read_fb2(src.read_bytes())
    name = a.name or slugify(doc.meta["title"], 60)
    ix = write_split(doc, out, a.max_chars, a.base, a.n, name)
    s, v = ix["stats"], ix["validation"]
    print("Документ: %s" % short(doc.meta["title"], 90))
    print("Файлов: %d, заголовков: %d (статей %d), изображений: %d, ~%d тыс. токенов всего"
          % (s["files"], s["headings"], s["articles"], s["images"], s["tokens_est"] // 1000))
    big = max(ix["files"], key=lambda f: f["chars"])
    print("Крупнейший файл: %s (%d симв., ~%d токенов)" % (big["path"], big["chars"], big["tokens_est"]))
    print("Целостность: %s | оглавление %d = заголовков %d | строки совпали: %s | не записано узлов: %d | крупных файлов: %d"
          % ("OK" if v["ok"] else "ПРОБЛЕМЫ", v["toc_entries"], v["headings_in_text"], v["lines_match"],
             len(v["nodes_not_written"]), len(v["oversize_files"])))
    print("Каталог: %s" % out)


def load_index(d):
    return json.loads((Path(d) / "index.json").read_text(encoding="utf-8"))


def cmd_outline(a):
    ix = load_index(a.dir)
    for n in ix["nodes"]:
        if n["level"] <= a.depth:
            where = "%s:%s" % (n["file"], n["line_start"]) if n["file"] else "-"
            print("%s%s  [%s, %d симв.]" % ("  " * (n["level"] - 1), short(n["title"], 80), where, n["chars"]))


def norm_query(q):
    q = q.strip().lower().replace("ст.", "статья ").replace("гл.", "глава ").replace("§", "параграф ")
    q = re.sub(r"\s+", " ", q).strip()
    m = re.match(r"^(\w+)\s+([ivxlcdm]+|\d+(?:[.\-]\d+)*)\.?$", q)
    if m and m.group(1) in KINDS:
        kind, num = m.group(1), m.group(2)
    elif re.fullmatch(r"\d+(?:[.\-]\d+)*", q):
        kind, num = "статья", q
    else:
        return None, None
    if re.fullmatch(r"[ivxlcdm]+", num):
        num = str(roman_to_int(num))
    return kind, num


def cmd_read(a):
    ix = load_index(a.dir)
    nodes = ix["nodes"]
    if a.id:
        found = [n for n in nodes if n["id"] == a.id]
    else:
        kind, num = norm_query(a.query or "")
        if not kind:
            sys.exit("Не понял запрос. Примеры: \"Статья 45\", \"ст. 45.1\", \"Глава 21\", \"Раздел VIII\"")
        found = [n for n in nodes if n["kind"] == kind and n["num"] == num]
    if not found:
        sys.exit("Не найдено. Смотрите структуру: outline %s" % a.dir)
    if len(found) > 1:
        print("Найдено несколько (%d), уточните через --id:" % len(found))
        byid = {n["id"]: n for n in nodes}
        for n in found:
            par = byid.get(n["parent"])
            print("  --id %s  %s  (внутри: %s)" % (n["id"], short(n["title"], 70), short(par["title"], 50) if par else "-"))
        return
    n = found[0]
    path = Path(a.dir) / n["file"]
    lines = path.read_text(encoding="utf-8").split("\n")
    chunk = lines[n["line_start"] - 1: n["line_end"]]
    if a.point:
        pt = re.escape(a.point.strip().rstrip("."))
        idx = next((i for i, ln in enumerate(chunk) if re.match(r"^%s\.\s" % pt, ln)), None)
        if idx is None:
            sys.exit("В «%s» нет пункта «%s.» (ищется строка, начинающаяся с «N. »). Подпункты «1)» читайте "
                     "в составе пункта или статьи целиком." % (short(n["title"], 50), a.point))
        end = next((i for i in range(idx + 1, len(chunk)) if re.match(r"^\d+\.\s", chunk[i]) or chunk[i].startswith("#")), len(chunk))
        chunk = [chunk[0], ""] + chunk[idx:end]  # заголовок статьи для контекста + нужный пункт
        print("<!-- %s : %s, пункт %s -->" % (n["file"], short(n["title"], 60), a.point))
    else:
        print("<!-- %s : строки %d–%d -->" % (n["file"], n["line_start"], n["line_end"]))
    print("\n".join(chunk))
    if n["split_into_files"]:
        kids = [k for k in nodes if k["parent"] == n["id"]]
        print("\n<!-- Часть слишком велика и разбита на файлы. Подчинённые части: -->")
        for k in kids:
            print("<!--   %s  [%s, %d симв.] -->" % (short(k["title"], 70), k["file"], k["chars"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("split")
    sp.add_argument("input")
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--max-chars", type=int, default=30000, help="целевой максимум символов в файле (~10 тыс. токенов)")
    sp.add_argument("--base")
    sp.add_argument("--n")
    sp.add_argument("--name")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_split)
    so = sub.add_parser("outline")
    so.add_argument("dir")
    so.add_argument("--depth", type=int, default=2)
    so.set_defaults(fn=cmd_outline)
    sr = sub.add_parser("read")
    sr.add_argument("dir")
    sr.add_argument("query", nargs="?")
    sr.add_argument("--id")
    sr.add_argument("--point", help="только пункт статьи, например 4 (для крупных статей)")
    sr.set_defaults(fn=cmd_read)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
