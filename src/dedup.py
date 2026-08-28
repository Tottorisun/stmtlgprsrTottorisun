# -*- coding: utf-8 -*-
"""
Слияние сырых записей нескольких источников в уникальные лиды.

Упрощённая версия подхода из RUSIMEX/leadgen/Hlebozavody_BY_KZ/scripts/dedup_engine.py
(там — полноценный Union-Find на 5+ сигналах для тысяч записей по 10 странам).
Здесь двух проходов достаточно для масштаба одного региона:

  1) точный ключ имя+город+улица/дом (normalize.make_dedupe_key)
  2) общий телефон между записями ОДНОГО города — но не если номер встречается
     у 3+ разных названий (типичный признак общего номера колл-центра/сети,
     а не одной и той же точки; тот же принцип, что в dedup_engine.py).
"""
from collections import defaultdict

from .normalize import make_dedupe_key, city_key


def _digits(phone):
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def merge_raw_leads(rows, region_name, log=print):
    """rows: список dict с ключами name, categories, city, address, phone(норм.
    строка), email, has_website, source, source_url. Возвращает список
    объединённых dict той же формы, плюс 'sources' (set) и 'source_urls' (list)."""
    groups = {}          # dedupe_key -> merged dict
    key_by_index = []

    for r in rows:
        k = make_dedupe_key(r["name"], r["city"], r["address"])
        key_by_index.append(k)
        if k not in groups:
            groups[k] = {**r, "sources": {r["source"]}, "source_urls": [r["source_url"]],
                         "dedupe_key": k}
        else:
            g = groups[k]
            g["sources"].add(r["source"])
            if r["source_url"] not in g["source_urls"]:
                g["source_urls"].append(r["source_url"])
            if r["phone"] and r["phone"] not in (g["phone"] or ""):
                g["phone"] = (g["phone"] + "; " + r["phone"]).strip("; ") if g["phone"] else r["phone"]
            if r["email"] and not g["email"]:
                g["email"] = r["email"]
            if r.get("website") and not g.get("website"):
                g["website"] = r["website"]
            if not g["address"] and r["address"]:
                g["address"] = r["address"]
            for c in r["categories"]:
                if c not in g["categories"]:
                    g["categories"].append(c)

    # второй проход: общий телефон в пределах одного города
    phone_owners = defaultdict(list)
    for k, g in groups.items():
        for p in (g["phone"] or "").split(";"):
            d = _digits(p)
            if len(d) >= 10:
                phone_owners[(city_key(g["city"] or g["address"]), d)].append(k)

    parent = {k: k for k in groups}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb, key=lambda z: str(z))] = min(ra, rb, key=lambda z: str(z))

    merged_by_phone = 0
    for (_, _), keys in phone_owners.items():
        uniq = sorted(set(keys))
        if len(uniq) == 2:  # ровно 2 записи делят номер и город -> сливаем
            union(uniq[0], uniq[1])
            merged_by_phone += 1
        # 3+ разных карточек с одним номером в одном городе -> похоже на общий
        # номер сети/колл-центра, не сливаем автоматически (тот же принцип,
        # что в dedup_engine.py: hub_phones)

    final_groups = defaultdict(list)
    for k in groups:
        final_groups[find(k)].append(k)

    out = []
    for root, keys in final_groups.items():
        base = groups[keys[0]]
        for k in keys[1:]:
            g = groups[k]
            base["sources"] |= g["sources"]
            for u in g["source_urls"]:
                if u not in base["source_urls"]:
                    base["source_urls"].append(u)
            if g["phone"] and g["phone"] not in (base["phone"] or ""):
                base["phone"] = (base["phone"] + "; " + g["phone"]).strip("; ") if base["phone"] else g["phone"]
            if g["email"] and not base["email"]:
                base["email"] = g["email"]
            if g.get("website") and not base.get("website"):
                base["website"] = g["website"]
            for c in g["categories"]:
                if c not in base["categories"]:
                    base["categories"].append(c)
        out.append(base)

    log(f"  [dedup] {region_name}: {len(rows)} сырых записей -> {len(groups)} по имени/адресу "
        f"-> {len(out)} после слияния по общему телефону (объединений: {merged_by_phone})")
    return out
