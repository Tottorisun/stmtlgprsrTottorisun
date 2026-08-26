# -*- coding: utf-8 -*-
"""Оркестратор: источники -> нормализация -> фильтр -> дедуп -> SQLite."""
from . import db
from .dedup import merge_raw_leads
from .normalize import normalize_phone, clean_email, is_dental_clinic, make_dedupe_key
from .models import FIELDS

SCRAPERS = {}  # заполняется лениво в run_region(), чтобы --sources yandex не тянул Playwright


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


def run_region(region_id, region_cfg, sources, conn, log=print):
    """Возвращает (leads: list[dict по models.FIELDS], stats: dict)."""
    stats = {"raw_by_source": {}, "raw_total": 0, "dental_filtered": 0,
             "no_website": 0, "with_contact": 0, "final_leads": 0}

    raw_all = []
    for src in sources:
        log(f"[{region_cfg['name']}] источник: {src}")
        mod = _get_scraper(src)
        raw = mod.scrape_region(region_cfg, log=log)
        stats["raw_by_source"][src] = len(raw)
        raw_all.extend(raw)
    stats["raw_total"] = len(raw_all)

    # нормализация + фильтр "это стоматология" + "без сайта" + "есть контакт"
    prepared = []
    for r in raw_all:
        if not is_dental_clinic(r["name"], r["categories"]):
            continue
        stats["dental_filtered"] += 1
        if r["has_website"]:
            continue
        stats["no_website"] += 1
        phones = [normalize_phone(p) for p in r["phones_raw"]]
        phones = sorted(set(p for p in phones if p))
        emails = [clean_email(e) for e in r["emails_raw"]]
        emails = sorted(set(e for e in emails if e))
        if not phones and not emails:
            continue
        stats["with_contact"] += 1
        prepared.append({
            "name": r["name"], "categories": r["categories"], "city": r["city"],
            "address": r["address"], "phone": "; ".join(phones),
            "email": emails[0] if emails else "", "has_website": False,
            "source": r["source"], "source_url": r["source_url"],
        })

    merged = merge_raw_leads(prepared, region_cfg["name"], log=log)
    stats["final_leads"] = len(merged)

    scraped_at = db.now_iso()
    leads = []
    for m in merged:
        lead = {
            "dedupe_key": m["dedupe_key"],
            "org_name": m["name"],
            "category": "; ".join(dict.fromkeys(m["categories"]))[:200],
            "region": region_cfg["name"],
            "city": m["city"],
            "address": m["address"],
            "phone": m["phone"],
            "email": m["email"],
            "has_website": 0,
            "source": "; ".join(sorted(m["sources"])),
            "source_url": "; ".join(m["source_urls"]),
            "scraped_at": scraped_at,
            "notes": "",
        }
        db.upsert_lead(conn, lead)
        leads.append(lead)
    conn.commit()
    return leads, stats
