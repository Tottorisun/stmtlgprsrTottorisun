# -*- coding: utf-8 -*-
"""
Оркестратор: источники -> нормализация -> фильтр -> дедуп -> SQLite.

Чек-пойнт по ГОРОДАМ, не по региону целиком (27.08.2026, по прямому запросу
владельца после разбора pipeline.py).

Было: run_region() собирал сырые записи по ВСЕМ городам ВСЕХ источников в
памяти (raw_all), и только после этого — нормализация/фильтр/дедуп/запись в
SQLite, с одним conn.commit() в самом конце. У каждого скрейпера уже есть
устойчивость к сбоям НА УРОВНЕ ОДНОГО ГОРОДА (сбойный город не роняет сбор
остальных, см. yandex_maps.py) — но эта устойчивость ничего не стоит, если
процесс целиком падает (крэш, kill, обрыв сети, сбой API — ровно то, что
только что произошло с этим же агентом посреди работы) после успешного сбора
5 из 6 городов, но ДО итогового цикла сохранения: тогда теряются данные по
ВСЕМ пяти городам, а не только по упавшему. Ничего не попадает на диск до
самого конца.

Стало: для каждого города — собрать сырые записи со ВСЕХ запрошенных
источников (кросс-источниковое слияние по-прежнему считается ВНУТРИ одной
пачки — то же самое поведение, что дало 38 автослияний на Краснодаре, когда
источники запускались вместе, просто теперь пачка "все источники по одному
городу", а не "все источники по всему региону") -> нормализация -> фильтр ->
merge_raw_leads -> запись в SQLite -> conn.commit() СРАЗУ, прежде чем перейти
к следующему городу. Если процесс упадёт на городе N, города 1..N-1 уже
зафиксированы в SQLite — проверено симуляцией сбоя, см. tests/test_pipeline_checkpoint.py.

Браузерная сессия (2ГИС/Google Maps) при этом открывается ОДИН раз на весь
регион, не на каждый город — переоткрывать Chrome (секунды) на каждый город
было бы намного медленнее без выигрыша в надёжности: чек-пойнт нужен на
уровне SQLite, не на уровне браузера. Единый интерфейс каждого модуля
источника — open_session(log) / scrape_city(session, city, log) /
close_session(session), см. src/scrapers/*.py.
"""
from . import db
from .dedup import merge_raw_leads
from .normalize import normalize_phone, clean_email, is_dental_clinic
from .scrapers.base import SourceBlocked

SCRAPERS = {}  # заполняется лениво, чтобы --sources yandex не тянул Playwright


def _get_scraper(name):
    if name not in SCRAPERS:
        if name == "yandex_maps":
            from .scrapers import yandex_maps as mod
        elif name == "2gis":
            from .scrapers import gis2 as mod
        elif name == "google_maps":
            from .scrapers import google_maps as mod
        else:
            raise KeyError(f"Неизвестный источник {name!r}")
        SCRAPERS[name] = mod
    return SCRAPERS[name]


def _prepare_and_merge(raw_records, region_name, city, stats, log, mode="no-site"):
    """Нормализация + фильтр 'это стоматология' + фильтр по режиму + дедуп
    ВНУТРИ этой пачки (один город, все запрошенные источники).

    mode:
      "no-site"  — старое поведение: оставляем клиники БЕЗ сайта, требуем
                   телефон/email (иначе лид непригоден для обхода). Критерий
                   отбора — отсутствие сайта.
      "has-site" — разворот 28.08.2026: оставляем клиники, У КОТОРЫХ САЙТ ЕСТЬ,
                   и забираем сам URL в лид (для последующего аудита сайта на
                   соответствие закону, src/site_audit.py). Телефон/email НЕ
                   обязателен: ключевой актив тут — адрес сайта, контакт для
                   письма позже находится на самом сайте; телефон с карточки
                   сохраняем, если он есть.
    """
    prepared = []
    for r in raw_records:
        if not is_dental_clinic(r["name"], r["categories"]):
            continue
        stats["dental_filtered"] += 1
        phones = sorted(set(p for p in (normalize_phone(x) for x in r["phones_raw"]) if p))
        emails = sorted(set(e for e in (clean_email(x) for x in r["emails_raw"]) if e))

        if mode == "has-site":
            website = (r.get("website") or "").strip()
            if not (r["has_website"] and website):
                continue
            stats["with_website"] += 1
            prepared.append({
                "name": r["name"], "categories": r["categories"], "city": r["city"],
                "address": r["address"], "phone": "; ".join(phones),
                "email": emails[0] if emails else "", "has_website": True,
                "website": website,
                "source": r["source"], "source_url": r["source_url"],
            })
        else:  # no-site
            if r["has_website"]:
                continue
            stats["no_website"] += 1
            if not phones and not emails:
                continue
            stats["with_contact"] += 1
            prepared.append({
                "name": r["name"], "categories": r["categories"], "city": r["city"],
                "address": r["address"], "phone": "; ".join(phones),
                "email": emails[0] if emails else "", "has_website": False,
                "website": "",
                "source": r["source"], "source_url": r["source_url"],
            })
    return merge_raw_leads(prepared, f"{region_name} / {city}", log=log)


def run_region(region_id, region_cfg, sources, conn, log=print, mode="no-site"):
    """Возвращает (leads: list[dict по models.FIELDS], stats: dict).

    mode — см. _prepare_and_merge: "no-site" (клиники без сайта, старое
    поведение) или "has-site" (клиники С сайтом + захват URL, разворот
    28.08.2026). Всё остальное — фильтр 'это стоматология', телефон, дедуп,
    чек-пойнт по городам — одинаково в обоих режимах.

    Чек-пойнт по городам: после каждого города — commit в conn. Если что-то
    падает посреди региона, уже обработанные города остаются в базе."""
    stats = {"raw_by_source": {}, "raw_total": 0, "dental_filtered": 0,
             "no_website": 0, "with_website": 0, "with_contact": 0, "final_leads": 0}
    modules = {src: _get_scraper(src) for src in sources}

    sessions = {}
    for src, mod in modules.items():
        log(f"[{region_cfg['name']}] открываю сессию источника: {src}")
        sessions[src] = mod.open_session(log=log)

    blocked_sources = set()
    leads = []
    try:
        for city in region_cfg["cities"]:
            raw_city = []
            for src, mod in modules.items():
                if src in blocked_sources:
                    continue
                log(f"[{region_cfg['name']}] источник: {src} | город: {city}")
                try:
                    raw = mod.scrape_city(sessions[src], city, log=log)
                except SourceBlocked as e:
                    log(f"  [{src}] ОСТАНОВКА по блоку — источник исключён из "
                        f"дальнейших городов региона (остальные источники продолжают): {e}")
                    blocked_sources.add(src)
                    raw = []
                except Exception as e:
                    # Сбой одного источника на одном городе не должен ронять ни
                    # город целиком (другие источники по нему всё равно соберём),
                    # ни тем более весь регион. Намеренно `except Exception`, а
                    # не голый `except:` — KeyboardInterrupt/SystemExit (и вообще
                    # любой BaseException не-Exception, естественный аналог
                    # "процесс прерван") этим НЕ ловится и продолжает
                    # распространяться наружу из run_region(), а не тонет здесь
                    # молча. Именно это и проверяет
                    # tests/test_pipeline_checkpoint.py: настоящий обрыв процесса
                    # должен прервать run_region, а не тихо продолжить как будто
                    # ничего не было — чек-пойнт по городам защищает от потери
                    # уже собранных данных, а не маскирует сам сбой.
                    log(f"  [{src}] ОШИБКА на городе {city}, пропускаю источник "
                        f"для этого города: {type(e).__name__}: {str(e)[:160]}")
                    raw = []
                stats["raw_by_source"][src] = stats["raw_by_source"].get(src, 0) + len(raw)
                raw_city.extend(raw)
            stats["raw_total"] += len(raw_city)

            merged_city = _prepare_and_merge(raw_city, region_cfg["name"], city, stats, log, mode=mode)
            stats["final_leads"] += len(merged_city)

            scraped_at = db.now_iso()
            for m in merged_city:
                lead = {
                    "dedupe_key": m["dedupe_key"],
                    "org_name": m["name"],
                    "category": "; ".join(dict.fromkeys(m["categories"]))[:200],
                    "region": region_cfg["name"],
                    "city": m["city"],
                    "address": m["address"],
                    "phone": m["phone"],
                    "email": m["email"],
                    "has_website": 1 if mode == "has-site" else 0,
                    "website": m.get("website", ""),
                    "source": "; ".join(sorted(m["sources"])),
                    "source_url": "; ".join(m["source_urls"]),
                    "scraped_at": scraped_at,
                    "notes": "",
                }
                db.upsert_lead(conn, lead)
                leads.append(lead)
            conn.commit()  # <-- ЧЕК-ПОЙНТ: город сохранён, что бы ни случилось дальше
            log(f"  [{region_cfg['name']}] город {city}: лидов сохранено "
                f"{len(merged_city)} (commit выполнен)")
    finally:
        for src, session in sessions.items():
            try:
                modules[src].close_session(session)
            except Exception as e:
                log(f"  [{src}] предупреждение при закрытии сессии: "
                    f"{type(e).__name__}: {str(e)[:160]}")

    return leads, stats
