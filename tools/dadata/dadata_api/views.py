# -*- coding: utf-8 -*-
"""Приведение ответов DaData к компактному виду. Поля, которых нет на тарифе, не выдумываются: они перечисляются
отдельно в not_in_tariff, чтобы было видно «данных нет» и «данные недоступны на тарифе»."""

from datetime import datetime, timezone

STATUS_RU = {
    "ACTIVE": "действует",
    "LIQUIDATING": "ликвидируется",
    "LIQUIDATED": "ликвидирована",
    "BANKRUPT": "банкротство",
    "REORGANIZING": "в процессе присоединения к другому юрлицу с последующей ликвидацией",
}
BANK_STATUS_RU = {"ACTIVE": "действует", "LIQUIDATING": "ликвидируется", "LIQUIDATED": "ликвидирован"}
BANK_TYPE_RU = {"BANK": "банк", "BANK_BRANCH": "филиал банка", "NKO": "НКО", "NKO_BRANCH": "филиал НКО", "RKC": "РКЦ",
                "CBR": "подразделение Банка России", "TREASURY": "казначейство", "OTHER": "иное"}
TAX_SYSTEM_RU = {"AUSN": "АУСН", "ESHN": "ЕСХН", "SRP": "СРП", "USN": "УСН"}
INVALIDITY_RU = {"PARTY": "по обращению лица или организации", "FTS": "по результатам проверки налоговой",
                 "COURT": "по решению суда", "OTHER": "по иной причине"}

# Что не приходит на бесплатном («Лёгком») тарифе. Ключи — пути в data, значения — подписи для пользователя.
EXTENDED_FIELDS = {
    "employee_count": "численность работников",
    "finance.tax_system": "система налогообложения",
    "okveds": "дополнительные коды ОКВЭД",
    "authorities": "налоговая, ПФР и ФСС",
}
MAXIMUM_FIELDS = {
    "founders": "учредители и доли",
    "managers": "руководители (список) и недостоверность сведений о них",
    "predecessors": "правопредшественники",
    "successors": "правопреемники",
    "capital": "уставный капитал",
    "finance.revenue": "выручка, доходы и расходы по бухгалтерской отчётности",
    "finance.debt": "недоимки и налоговые штрафы",
    "licenses": "лицензии",
    "documents": "реестр МСП и учётные документы",
    "phones": "телефоны",
    "emails": "адреса электронной почты",
    "sites": "сайты",
    "invalid": "признак недостоверных сведений",
}


def ms_date(ms):
    """Даты DaData — миллисекунды Unix; в вывод идёт дата ГГГГ-ММ-ДД."""
    if ms in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _dig(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _present(d, path):
    return _dig(d, path) not in (None, [], {}, "")


def not_in_tariff(data, tier):
    """Подписи полей, которых нет в ответе из-за тарифа. Для полного тарифа и неизвестного тарифа пусто."""
    if tier == "base":
        groups = {**EXTENDED_FIELDS, **MAXIMUM_FIELDS}
    elif tier == "extended":
        groups = MAXIMUM_FIELDS
    else:
        return []
    return [label for path, label in groups.items() if not _present(data, path)]


def detect_tier(data):
    """Уровень тарифа по набору полей в ответе по компании с богатой карточкой."""
    if any(_present(data, p) for p in ("founders", "managers", "finance.revenue", "licenses", "invalid")):
        return "maximum"
    if any(_present(data, p) for p in ("employee_count", "okveds", "authorities", "finance.tax_system")):
        return "extended"
    return "base"


def _invalidity(inv):
    if not inv:
        return None
    out = {"reason": INVALIDITY_RU.get(inv.get("code"), inv.get("code"))}
    dec = inv.get("decision")
    if dec:
        out["decision"] = {"court": dec.get("court_name"), "number": dec.get("number"), "date": ms_date(dec.get("date"))}
    return out


def _person(x):
    """Наименование учредителя или руководителя: организация — строкой, физлицо — ФИО (объект или строка)."""
    fio = x.get("fio")
    if isinstance(fio, dict):
        fio = " ".join(p for p in (fio.get("surname"), fio.get("name"), fio.get("patronymic")) if p)
    return x.get("name") or fio


def _share(s):
    if not s:
        return None
    if s.get("type") == "FRACTION":
        return "%s/%s" % (s.get("numerator"), s.get("denominator"))
    if s.get("type") == "PERCENT":
        return "%s%%" % s.get("value")
    return str(s.get("value"))


def _address(addr):
    a = addr or {}
    ad = a.get("data") or {}
    return {"value": a.get("value"), "with_index": a.get("unrestricted_value"), "as_in_egrul": ad.get("source"),
            "check_code": ad.get("qc"), "invalidity": _invalidity(a.get("invalidity"))}


def company_view(item, tier=None):
    """Одна запись findById/party или suggest/party -> компактное описание."""
    d = item.get("data") or {}
    name = d.get("name") or {}
    state = d.get("state") or {}
    mgmt = d.get("management") or {}
    fio = d.get("fio") or {}
    v = {
        "name": {"full": name.get("full_with_opf"), "short": name.get("short_with_opf")},
        "type": d.get("type"),
        "inn": d.get("inn"), "kpp": d.get("kpp"), "ogrn": d.get("ogrn"), "ogrn_date": ms_date(d.get("ogrn_date")),
        "opf": (d.get("opf") or {}).get("short"),
        "branch_type": d.get("branch_type"), "branch_count": d.get("branch_count"),
        "status": {"code": state.get("status"), "text": STATUS_RU.get(state.get("status"), state.get("status")),
                   "registration_date": ms_date(state.get("registration_date")),
                   "liquidation_date": ms_date(state.get("liquidation_date")),
                   "actuality_date": ms_date(state.get("actuality_date"))},
        "management": {"name": mgmt.get("name"), "post": mgmt.get("post"), "start_date": ms_date(mgmt.get("start_date"))},
        "address": _address(d.get("address")),
        "okved": {"main": d.get("okved"), "version": d.get("okved_type")},
        "stat_codes": {k: d.get(k) for k in ("okpo", "oktmo", "okato", "okogu", "okfs") if d.get(k)},
    }
    if d.get("type") == "INDIVIDUAL":
        v["individual"] = {"fio": " ".join(x for x in (fio.get("surname"), fio.get("name"), fio.get("patronymic")) if x)}
        if d.get("citizenship"):
            v["individual"]["citizenship"] = (d["citizenship"].get("name") or {}).get("short")
    # Расширенный тариф
    if _present(d, "employee_count"):
        v["employee_count"] = d["employee_count"]
    ts = _dig(d, "finance.tax_system")
    if ts:
        v["tax_system"] = TAX_SYSTEM_RU.get(ts, ts)
    if d.get("okveds"):
        v["okved_additional"] = [{"code": o.get("code"), "name": o.get("name"), "main": o.get("main")} for o in d["okveds"]]
    if d.get("authorities"):
        v["authorities"] = {k: {"code": a.get("code"), "name": a.get("name"), "address": a.get("address")}
                            for k, a in d["authorities"].items() if a}
    # Максимальный тариф
    if d.get("founders"):
        v["founders"] = [{"name": _person(f), "inn": f.get("inn"), "ogrn": f.get("ogrn"),
                          "type": f.get("type"), "share": _share(f.get("share")), "since": ms_date(f.get("start_date")),
                          "invalidity": _invalidity(f.get("invalidity"))} for f in d["founders"]]
    if d.get("managers"):
        v["managers"] = [{"name": _person(m), "post": m.get("post"), "inn": m.get("inn"),
                          "since": ms_date(m.get("start_date")), "invalidity": _invalidity(m.get("invalidity"))}
                         for m in d["managers"]]
    for key in ("predecessors", "successors"):
        if d.get(key):
            v[key] = [{"name": x.get("name"), "inn": x.get("inn"), "ogrn": x.get("ogrn")} for x in d[key]]
    if d.get("capital"):
        v["capital"] = d["capital"]
    fin = d.get("finance") or {}
    if any(fin.get(k) is not None for k in ("revenue", "income", "expense", "debt", "penalty")):
        v["finance"] = {k: fin.get(k) for k in ("year", "revenue", "income", "expense", "debt", "penalty")}
    if d.get("licenses"):
        v["licenses"] = [{"number": l.get("number"), "issued": ms_date(l.get("issue_date")), "authority": l.get("issue_authority"),
                          "valid_from": ms_date(l.get("valid_from")), "valid_to": ms_date(l.get("valid_to")),
                          "suspended": ms_date(l.get("suspend_date")), "activities": l.get("activities")} for l in d["licenses"]]
    smb = (d.get("documents") or {}).get("smb")
    if smb:
        v["smb"] = {"category": smb.get("category"), "since": ms_date(smb.get("issue_date"))}
    contacts = {}
    for key, sub in (("phones", "source"), ("emails", "source"), ("sites", "domain")):
        if d.get(key):
            contacts[key] = [(x.get("data") or {}).get(sub) or x.get("value") for x in d[key]]
    if contacts:
        v["contacts"] = contacts
    if "invalid" in d and d.get("invalid"):
        v["invalid"] = True
    v["warnings"] = warnings(v)
    na = not_in_tariff(d, tier)
    if na:
        v["not_in_tariff"] = na
    return v


def warnings(v):
    """Что юрист должен увидеть сразу, не вчитываясь в карточку."""
    w = []
    code = v["status"]["code"]
    if code and code != "ACTIVE":
        w.append("Статус: %s." % v["status"]["text"])
    if str(v["address"]["check_code"]) in ("1", "3"):
        w.append("Адрес распознан неуверенно (стандартизация DaData): сверить с ЕГРЮЛ.")
    if v.get("invalid"):
        w.append("ФНС признала недостоверными сведения об учредителе, руководителе или адресе.")
    if v["type"] == "INDIVIDUAL":
        w.append("Для ИП в адресе указан только город проживания, не адрес регистрации.")
    return w


def bank_view(item):
    d = item.get("data") or {}
    name = d.get("name") or {}
    state = d.get("state") or {}
    cbr = d.get("cbr") or {}
    return {"name": {"payment": name.get("payment"), "short": name.get("short")}, "bic": d.get("bic"), "swift": d.get("swift"),
            "inn": d.get("inn"), "kpp": d.get("kpp"), "registration_number": d.get("registration_number"),
            "correspondent_account": d.get("correspondent_account"), "type": BANK_TYPE_RU.get((d.get("opf") or {}).get("type")),
            "payment_city": d.get("payment_city"), "address": (d.get("address") or {}).get("unrestricted_value"),
            "cbr_unit": (cbr.get("name") or {}).get("payment"),
            "status": {"code": state.get("status"), "text": BANK_STATUS_RU.get(state.get("status"), state.get("status")),
                       "registration_date": ms_date(state.get("registration_date")),
                       "liquidation_date": ms_date(state.get("liquidation_date"))}}


def court_view(item):
    d = item.get("data") or {}
    return {"code": d.get("code"), "name": d.get("name"), "type": d.get("court_type"), "type_name": d.get("court_type_name"),
            "inn": d.get("inn"), "address": d.get("address"), "legal_address": d.get("legal_address"), "website": d.get("website")}


def tax_office_view(item):
    d = item.get("data") or {}
    return {"code": d.get("code"), "name": d.get("name"), "address": d.get("address"), "phone": d.get("phone"),
            "comment": d.get("comment"),
            "payment": {"recipient": d.get("payment_name"), "inn": d.get("inn"), "kpp": d.get("kpp"), "oktmo": d.get("oktmo"),
                        "bank": d.get("bank_name"), "bic": d.get("bank_bic"),
                        "correspondent_account": d.get("bank_correspondent_account"), "account": d.get("bank_account")},
            "registering_office": {"code": d.get("parent_code"), "name": d.get("parent_name"), "address": d.get("parent_address"),
                                   "phone": d.get("parent_phone")}}
