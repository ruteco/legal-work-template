# -*- coding: utf-8 -*-
"""Карточка контрагента (markdown) и блок реквизитов для договора."""

from .features import NOT_COVERED


def _dash(x):
    return x if x not in (None, "", []) else "—"


def requisites_text(v):
    """Блок «Реквизиты стороны» для договора. Расчётный счёт DaData по организациям не отдаёт: берётся у контрагента."""
    n = v["name"]
    if v["type"] == "INDIVIDUAL":
        lines = [(v.get("individual") or {}).get("fio") and "Индивидуальный предприниматель %s" % v["individual"]["fio"] or n["full"],
                 "ИНН %s" % _dash(v["inn"]), "ОГРНИП %s" % _dash(v["ogrn"])]
        if v["address"]["value"]:
            lines.append("Город проживания: %s (адрес регистрации в открытых данных не публикуется)" % v["address"]["value"])
    else:
        lines = [n["full"] or n["short"], "ИНН %s / КПП %s" % (_dash(v["inn"]), _dash(v["kpp"])), "ОГРН %s" % _dash(v["ogrn"]),
                 "Юридический адрес: %s" % _dash(v["address"]["with_index"] or v["address"]["value"])]
        m = v["management"]
        if m.get("name"):
            lines.append("Руководитель: %s %s" % (_dash(m.get("post")), m["name"]))
    return "\n".join(lines)


def _tbl(rows):
    return "\n".join(["| Поле | Значение |", "|---|---|"] + ["| %s | %s |" % (k, str(_dash(val)).replace("|", "/")) for k, val in rows])


def report_markdown(v, fetched_at, cached, tier):
    n = v["name"]
    st = v["status"]
    out = ["# Карточка контрагента: %s" % (n["short"] or n["full"] or v["inn"]), "",
           "Источник: DaData (данные ЕГРЮЛ/ЕГРИП и других реестров ФНС), получено %s%s. Это справочная выдержка «как есть», "
           "не выписка ЕГРЮЛ: для суда и сделки нужна выписка на дату." % (fetched_at, " (из локального кеша)" if cached else ""),
           ""]
    if v["warnings"]:
        out += ["## Внимание", ""] + ["- " + w for w in v["warnings"]] + [""]
    rows = [("Полное наименование", n["full"]), ("Краткое наименование", n["short"]),
            ("Тип", "индивидуальный предприниматель" if v["type"] == "INDIVIDUAL" else "юридическое лицо"),
            ("ИНН", v["inn"]), ("КПП", v["kpp"]), ("ОГРН", v["ogrn"]), ("Дата присвоения ОГРН", v["ogrn_date"]),
            ("Подразделение", {"MAIN": "головная организация", "BRANCH": "филиал"}.get(v["branch_type"], v["branch_type"])),
            ("Филиалов", v["branch_count"]), ("Статус", st["text"]), ("Дата регистрации", st["registration_date"]),
            ("Дата ликвидации", st["liquidation_date"]), ("Данные ФНС актуальны на", st["actuality_date"]),
            ("Адрес", v["address"]["with_index"] or v["address"]["value"]),
            ("Адрес как в ЕГРЮЛ", v["address"]["as_in_egrul"]),
            ("Руководитель", " ".join(x for x in (v["management"].get("post"), v["management"].get("name")) if x)),
            ("С какой даты", v["management"].get("start_date")),
            ("Основной ОКВЭД", v["okved"]["main"])]
    if v.get("individual"):
        rows.insert(3, ("ФИО предпринимателя", v["individual"]["fio"]))
    out += ["## Реквизиты и состояние", "", _tbl(rows), ""]

    extra = []
    for key, label in (("employee_count", "Численность работников"), ("tax_system", "Налоговый режим")):
        if v.get(key) is not None:
            extra.append((label, v[key]))
    if v.get("okved_additional"):
        extra.append(("Дополнительные ОКВЭД", "; ".join("%s %s" % (o["code"], o["name"]) for o in v["okved_additional"])))
    for k, a in (v.get("authorities") or {}).items():
        extra.append(("Орган (%s)" % k, "%s, %s" % (a.get("name"), a.get("address"))))
    if v.get("capital"):
        extra.append(("Уставный капитал", "%s" % (v["capital"].get("value"),)))
    if v.get("finance"):
        f = v["finance"]
        extra.append(("Бухгалтерская отчётность за %s" % f.get("year"),
                      "выручка %s, расходы %s, недоимки %s, штрафы %s" % tuple(_dash(f.get(k)) for k in ("revenue", "expense", "debt", "penalty"))))
    if v.get("smb"):
        extra.append(("Реестр МСП", "%s с %s" % (v["smb"]["category"], v["smb"]["since"])))
    if extra:
        out += ["## Дополнительные сведения (открыты тарифом)", "", _tbl(extra), ""]
    if v.get("founders"):
        out += ["## Учредители", ""] + ["- %s%s%s" % (f["name"], ", доля %s" % f["share"] if f["share"] else "",
                                                       " (сведения признаны недостоверными)" if f.get("invalidity") else "")
                                         for f in v["founders"]] + [""]
    if v.get("managers"):
        out += ["## Руководители", ""] + ["- %s, %s%s" % (m["name"], _dash(m["post"]),
                                                          " (сведения признаны недостоверными)" if m.get("invalidity") else "")
                                          for m in v["managers"]] + [""]
    if v.get("licenses"):
        out += ["## Лицензии", ""] + ["- № %s, выдана %s, действует %s — %s" % (l["number"], l["issued"], l["valid_from"], _dash(l["valid_to"]))
                                       for l in v["licenses"]] + [""]
    if v.get("not_in_tariff"):
        out += ["## Недоступно на тарифе пользователя", "",
                "На текущем тарифе DaData не отдаёт: %s. Это не значит, что таких сведений нет." % "; ".join(v["not_in_tariff"]), ""]
    out += ["## Чего в карточке нет и где смотреть", ""] + ["- " + x for x in NOT_COVERED] + [""]
    return "\n".join(out)
