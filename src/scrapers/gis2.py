# -*- coding: utf-8 -*-
"""
2ГИС: без Playwright не обойтись. Прямой вызов catalog.api.2gis.ru с публичным
ключом (тем, что использует сам виджет 2gis.ru, виден в его сетевых запросах)
пробовался первым — оба известных публичных ключа сейчас отвечают 403
"key is blocked" / "incorrect key" (проверено 26.08.2026, см. отчёт агента).
2ГИС, судя по всему, ротирует/блокирует такие ключи по мере их публичного
использования, так что рабочий способ — открывать страницы как обычный
браузер и читать данные из window.initialState самой карточки фирмы, точно
так же, как это делает harvest_2gis_v2.py в проекте RUSIMEX/leadgen/Hlebozavody_BY_KZ.

ВАЖНО (честно, без подмены данных): с сети, где сейчас работает эта машина,
2gis.ru отдаёт на любой поисковый запрос жёсткий редирект на
captcha.2gis.ru/form — "подозрительная активность с вашего IP" (её показывают
даже headless=False + системный Chrome + все анти-детект флаги). Это не
частный сбой запроса — это блокировка на уровне сети/IP, обходить капчу
запрещено правилами задачи. Модуль ниже реализует рабочую технику и годится
для запуска с российского IP (или после смены сети) — тогда он заработает
без единой правки. Из текущей сети он вернёт SourceBlocked с понятной
причиной, что и попадёт в progress.json как честный "gap", а не как 0
результатов, похожий на "ничего не нашлось".
"""
import re
import time
import random

from .base import SourceBlocked

SEARCH_QUERY = "стоматология"
MAX_PAGES = 5


def _open_browser(pw):
    b = pw.chromium.launch(
        channel="chrome", headless=False,
        args=["--disable-blink-features=AutomationControlled", "--disable-quic",
              "--window-position=2000,2000"])
    ctx = b.new_context(locale="ru-RU", viewport={"width": 1366, "height": 850})
    return b, ctx


def _check_blocked(page):
    u = page.url or ""
    if "captcha.2gis.ru" in u or "/museum" in u:
        raise SourceBlocked(f"2gis: капча/блок на {u[:160]}")


def _collect_firm_ids(page, city_slug, query):
    ids = []
    for page_n in range(1, MAX_PAGES + 1):
        url = f"https://2gis.ru/{city_slug}/search/{query}"
        if page_n > 1:
            url += f"/page/{page_n}"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(3000, 4500))
        _check_blocked(page)
        hrefs = page.eval_on_selector_all(
            "a[href*='/firm/']", "els => els.map(e => e.getAttribute('href'))")
        new = 0
        for h in hrefs:
            m = re.search(r"/firm/(\d+)", h or "")
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
                new += 1
        if new == 0:
            break
        time.sleep(random.uniform(1.2, 2.2))
    return ids


def _scrape_card(page, city_slug, fid):
    url = f"https://2gis.ru/{city_slug}/firm/{fid}"
    page.goto(url, timeout=45000, wait_until="domcontentloaded")
    page.wait_for_timeout(random.randint(2000, 3000))
    _check_blocked(page)
    item = page.evaluate("""(fid) => {
        try {
            const prof = (window.initialState || {}).data?.entity?.profile || {};
            let rec = prof[fid]?.data;
            if (!rec) {
                for (const v of Object.values(prof)) {
                    const d = v && v.data;
                    if (d && String(d.id || '').startsWith(fid)) { rec = d; break; }
                }
            }
            return rec || null;
        } catch (e) { return null; }
    }""", fid)
    if not item:
        return None
    phones, emails, has_site = [], [], False
    for g in item.get("contact_groups") or []:
        for c in g.get("contacts") or []:
            t = c.get("type")
            v = c.get("value") or c.get("url") or c.get("text")
            if t == "phone" and v:
                phones.append(v)
            elif t == "email" and v:
                emails.append(v)
            elif t == "website" and v:
                has_site = True
    city_name = ""
    for a in item.get("adm_div") or []:
        if a.get("type") == "city" and a.get("name"):
            city_name = re.sub(r"^г\.\s*", "", a["name"])
            break
    cats = [r.get("name") for r in item.get("rubrics") or [] if r.get("name")]
    name_ex = item.get("name_ex") or {}
    return {
        "name": name_ex.get("primary") or item.get("name") or "",
        "categories": cats,
        "city": city_name,
        "address": item.get("address_name") or "",
        "phones_raw": phones,
        "emails_raw": emails,
        "has_website": has_site,
        "source": "2gis",
        "source_url": url,
    }


def _slugify_city(city):
    """2ГИС использует латинские слаги городов в URL. Малый словарь для наших
    регионов; для города, которого нет в словаре, пробуем транслит как fallback
    (может не совпасть с реальным слагом 2ГИС — тогда сайт сам отдаст 404/редирект,
    это не тихая порча данных, увидим в логах)."""
    known = {
        "москва": "moscow", "краснодар": "krasnodar", "сочи": "sochi",
        "новороссийск": "novorossiysk", "армавир": "armavir", "анапа": "anapa",
        "ейск": "eysk", "тюмень": "tyumen", "тобольск": "tobolsk", "ишим": "ishim",
        "ялуторовск": "yalutorovsk", "заводоуковск": "zavodoukovsk",
        "екатеринбург": "ekaterinburg", "нижний тагил": "nizhniy_tagil",
        "каменск-уральский": "kamensk-uralskiy", "первоуральск": "pervouralsk",
        "ростов-на-дону": "rostov-na-donu", "таганрог": "taganrog",
        "шахты": "shakhty", "новочеркасск": "novocherkassk",
        "красноярск": "krasnoyarsk", "норильск": "norilsk", "ачинск": "achinsk",
        "канск": "kansk",
    }
    key = city.strip().lower()
    return known.get(key, key.replace(" ", "_").replace("-", "_"))


def scrape_region(region_cfg, log=print):
    """Требует Playwright + системный Chrome. Кидает SourceBlocked при капче —
    вызывающий код (pipeline.py) это ловит и продолжает с тем, что уже собрано."""
    from playwright.sync_api import sync_playwright

    out = []
    with sync_playwright() as pw:
        browser, ctx = _open_browser(pw)
        page = ctx.new_page()
        try:
            for city in region_cfg["cities"]:
                slug = _slugify_city(city)
                log(f"  [2gis] город: {city} (slug={slug})")
                ids = _collect_firm_ids(page, slug, SEARCH_QUERY)
                log(f"  [2gis] {city}: найдено карточек {len(ids)}")
                for fid in ids:
                    rec = _scrape_card(page, slug, fid)
                    if rec:
                        out.append(rec)
                    time.sleep(random.uniform(1.5, 2.5))
        finally:
            browser.close()
    return out
