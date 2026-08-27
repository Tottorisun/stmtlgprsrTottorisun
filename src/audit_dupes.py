# -*- coding: utf-8 -*-
"""
Второй, независимый проход по возможным дублям — ПОСЛЕ автоматического
слияния в dedup.py, не вместо него.

Добавлено 27.08.2026 по прямому запросу владельца: сверить с тем, что уже
решено в RUSIMEX/leadgen/Hlebozavody_BY_KZ. Там есть отдельный скрипт именно
на этот случай — scripts/audit_dupes_deep2.py, "основной аудит дублей по ВСЕМ
10 странам" (см. QUALITY_RULES.md, п.1 и п.3) — и он идёт ПОСЛЕ
dedup_engine.py, а не заменяет его. Причина явно записана в QUALITY_RULES.md:
нечёткое сходство имён/вложенность названий/общий сайт — "не хард-фейл: дают
много ожидаемых ложных срабатываний на легитимных сетях с филиалами (Спартак,
Красный пищевик, Хлебозавод №1/2/3... — это разные физические точки, не
дубли), смотреть глазами". То есть даже в куда более зрелом пайплайне (10
стран, месяцы итераций) нечёткие сигналы НЕ сливают автоматически — их
выводят человеку на проверку, потому что для стоматологий та же логика
верна один в один: у сети клиник может быть несколько РАЗНЫХ филиалов с
похожими/вложенными названиями в одном городе, которые не должны схлопнуться
в один лид.

Из-за этого этот модуль устроен как ОТЧЁТ, а не как ещё один шаг слияния:
печатает то, что выглядит подозрительно похожим, для ручного просмотра
(и/или отдельным листом в .xlsx, см. export_xlsx.py), но ничего не меняет
в базе сам. Один найденный вживую случай ("Абсолют-Дент" — SEO-имя с Google
Maps не совпало с чистым именем на Яндексе, разные форматы адреса, разные
телефоны — см. README) уже пофиксили точечно в google_maps.py и вручную в
базе; этот модуль — сеть на ВСЁ остальное, что похожая по духу, но более
широкая эвристика может поймать и что точечный фикс одного случая не решает.
"""
import re
import difflib
from collections import defaultdict

from .normalize import name_key, city_key

# Слишком общие названия — реальный кейс из живого прогона 27.08.2026: клиника,
# буквально названная "Стоматология" (Тюмень, ул. Бакинских Комиссаров, 1),
# оказалась "вложена" почти в КАЖДУЮ другую запись региона (любое название вида
# "Стоматология Х" содержит "стоматология" как подстроку) — 10 из 19 найденных
# групп были только из-за этого одного названия. Тот же приём, что и
# GENERIC_NAMES в merge_lib.py: общее название не даёт сигнала "вложенность"
# само по себе, нужен более специфичный сигнал (телефон/домен) или точное имя.
#
# "Стоматолог" (без "-ия") добавлен 27.08.2026 по тому же самому паттерну,
# найденному живьём в Краснодарском крае: клиника с таким названием в Сочи
# (микрорайон Центральный) и отдельная клиника с таким же названием в Анапе
# (улица Омелькова) дали 14 ложных "вложенных" совпадений на двоих — тот же
# класс проблемы, что и "Стоматология" в Тюмени, просто другое склонение.
GENERIC_NAMES = {name_key(n) for n in (
    "стоматология", "стоматологическая клиника", "стоматологическая поликлиника",
    "семейная стоматология", "детская стоматология", "зубной врач", "дантист",
    "стоматолог",
)}


def _digits(phone):
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def _phones_of(lead):
    return {d for d in (_digits(p) for p in str(lead.get("phone") or "").split(";")) if len(d) >= 10}


def _site_domain(url):
    s = re.sub(r"^https?://(www\.)?", "", str(url or "").lower()).split("/")[0].strip()
    return s if len(s) > 5 and "." in s else ""


def find_possible_duplicates(leads):
    """leads: список dict со схемой models.FIELDS (обычно все лиды одного
    региона). Возвращает список групп-подозрений — каждая группа: список
    лидов + 'signal' (почему их свели рядом). Каждый лид может попасть в
    несколько групп по разным сигналам — это НЕ ошибка, это разные основания
    для проверки одной и той же пары."""
    groups = []

    # 1. Нечёткое сходство названий в одном городе (порог 0.90 — как в
    #    audit_dupes_deep2.py, п.1)
    by_city = defaultdict(list)
    for lead in leads:
        by_city[city_key(lead.get("city") or lead.get("address"))].append(lead)
    for city, lst in by_city.items():
        if not city or len(lst) < 2:
            continue
        used = set()
        for i in range(len(lst)):
            if i in used:
                continue
            grp = [lst[i]]
            for j in range(i + 1, len(lst)):
                if j in used:
                    continue
                a, b = name_key(lst[i]["org_name"]), name_key(lst[j]["org_name"])
                if len(a) < 8 or len(b) < 8:
                    continue
                if a in GENERIC_NAMES or b in GENERIC_NAMES:
                    continue  # см. GENERIC_NAMES выше
                if difflib.SequenceMatcher(None, a, b).ratio() >= 0.90:
                    grp.append(lst[j])
                    used.add(j)
            if len(grp) > 1:
                groups.append({"signal": "похожие_названия", "leads": grp})

    # 2. Вложенность названий в одном городе ("Дент" ⊂ "Дент Люкс")
    for city, lst in by_city.items():
        if not city or len(lst) < 2:
            continue
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = name_key(lst[i]["org_name"]), name_key(lst[j]["org_name"])
                if a in GENERIC_NAMES or b in GENERIC_NAMES:
                    continue  # см. GENERIC_NAMES выше — общее название не сигнал
                if len(a) >= 8 and len(b) >= 8 and a != b and (a in b or b in a):
                    groups.append({"signal": "вложенное_название", "leads": [lst[i], lst[j]]})

    # 3. Частичное пересечение телефонов (хотя бы один общий номер) — ШИРЕ,
    #    чем точное совпадение всей строки phone в dedup.py: ловит случаи вроде
    #    "+7 982 XXX; +7 345 YYY" против отдельной записи с одним из этих номеров
    phone_index = defaultdict(list)
    for lead in leads:
        for p in _phones_of(lead):
            phone_index[p].append(lead)
    seen_pairs = set()
    for p, lst in phone_index.items():
        if len(lst) < 2:
            continue
        key = tuple(sorted(id(x) for x in lst))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        groups.append({"signal": "общий_телефон", "leads": lst})

    # 4. Общий домен в source_url — та же организация, разные карточки/источники
    site_map = defaultdict(list)
    for lead in leads:
        for url in str(lead.get("source_url") or "").split(";"):
            d = _site_domain(url)
            if d and "2gis" not in d and "yandex" not in d and "google" not in d:
                site_map[d].append(lead)
    for d, lst in site_map.items():
        uniq = list({id(x): x for x in lst}.values())
        if len(uniq) > 1:
            groups.append({"signal": "общий_домен_в_ссылке", "leads": uniq})

    return groups


def format_report(groups, log=print):
    """Печатает отчёт для ручного просмотра. НЕ меняет базу."""
    if not groups:
        log("  [audit_dupes] подозрений на дубли не найдено")
        return
    log(f"  [audit_dupes] подозрений на возможные дубли: {len(groups)} "
        f"(это КАНДИДАТЫ на ручную проверку, не подтверждённые дубли — "
        f"легитимные сети с филиалами дадут ожидаемые ложные срабатывания, "
        f"см. docstring модуля)")
    for g in groups:
        log(f"    --- сигнал: {g['signal']} ---")
        for lead in g["leads"]:
            log(f"      {lead.get('org_name','')[:40]!r} | {lead.get('city','')} | "
                f"{lead.get('address','')[:40]} | {lead.get('phone','')[:30]} | "
                f"{lead.get('source','')}")
