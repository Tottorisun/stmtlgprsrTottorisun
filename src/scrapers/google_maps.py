# -*- coding: utf-8 -*-
"""
Google Maps — дополнительный источник ("на всякий случай", ниже приоритетом,
чем 2ГИС/Яндекс — так и в задаче). Официального API-ключа (Places API) нет и
не запрашивался — используем тот же приём, что и остальные скрейперы: обычная
страница поиска + прокрутка ленты + карточка места, без ключа и без обхода
защиты. Метод и селекторы взяты из
D:\\Мои разработки\\RUSIMEX\\leadgen\\Hlebozavody_BY_KZ\\scripts\\harvest_gmaps_by_kz.py
(проверенная связка селекторов на актуальном Google Maps).

Проверено вживую 26.08.2026: поиск "стоматология Тюмень" отдаёт ленту
результатов без капчи с текущей (не-российской) сети — в отличие от 2ГИС.
Возможны региональные различия — Google по IP может показать другую версию
интерфейса; при капче/блоке модуль останавливается (SourceBlocked), а не
подделывает данные.
"""
import re
import time
import random
from urllib.parse import quote

from .base import SourceBlocked

SEARCH_QUERY = "стоматология"
MAX_SCROLLS = 25
MAX_RESULTS_PER_CITY = 80


def _is_blocked(page):
    u = page.url or ""
    if "/sorry/" in u or "google.com/sorry" in u:
        return True
    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""
    return "unusual traffic" in title or "sorry" in title


def _handle_consent(page):
    try:
        for sel in ["button:has-text('Отклонить все')", "button:has-text('Reject all')"]:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=4000)
                page.wait_for_timeout(1500)
                return
    except Exception:
        pass


def _scroll_feed(page):
    last, stable = -1, 0
    for _ in range(MAX_SCROLLS):
        try:
            page.locator("div[role='feed']").evaluate("el => el.scrollBy(0, el.scrollHeight)")
        except Exception:
            break
        time.sleep(random.uniform(1.4, 2.2))
        try:
            if page.get_by_text("Вы достигли конца списка").count() > 0:
                break
            cnt = page.locator("a.hfpxzc").count()
        except Exception:
            break
        if cnt >= MAX_RESULTS_PER_CITY:
            break
        if cnt == last:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        last = cnt


def _parse_card(page, city):
    try:
        page.wait_for_selector("h1", timeout=15000)
        name = page.locator("h1").first.inner_text(timeout=4000).strip()
    except Exception:
        return None
    if not name:
        return None

    def first(sel, attr=None):
        try:
            loc = page.locator(sel)
            if loc.count() == 0:
                return ""
            return (loc.first.get_attribute(attr) if attr else loc.first.inner_text(timeout=3000)) or ""
        except Exception:
            return ""

    cat = first("button.DkEaL").strip()
    addr = re.sub(r"^(Адрес|Address):\s*", "", first("button[data-item-id='address']", "aria-label") or "").strip()
    phones = []
    try:
        for el in page.locator("button[data-item-id^='phone:tel:']").all():
            di = el.get_attribute("data-item-id") or ""
            tel = di.split("phone:tel:", 1)[-1].strip()
            if tel and tel not in phones:
                phones.append(tel)
    except Exception:
        pass
    website = first("a[data-item-id='authority']", "href")
    has_site = bool(website) and not website.startswith("https://www.google.com")
    return {
        "name": name,
        "categories": [cat] if cat else [],
        "city": city,
        "address": addr,
        "phones_raw": phones,
        "emails_raw": [],
        "has_website": has_site,
        "source": "google_maps",
        "source_url": page.url,
    }


def _search_city(page, city, log):
    url = f"https://www.google.com/maps/search/{quote(SEARCH_QUERY + ' ' + city)}/?hl=ru"
    page.goto(url, timeout=45000, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    _handle_consent(page)
    if _is_blocked(page):
        raise SourceBlocked(f"google_maps: блок/капча на {page.url[:160]}")

    out = []
    if "/maps/place/" in page.url:
        rec = _parse_card(page, city)
        return [rec] if rec else []

    try:
        page.wait_for_selector("div[role='feed']", timeout=12000)
    except Exception:
        return out
    _scroll_feed(page)
    if _is_blocked(page):
        raise SourceBlocked(f"google_maps: блок/капча в середине прокрутки, {city}")

    hrefs = page.eval_on_selector_all("a.hfpxzc", "els => els.map(e => e.href)")
    log(f"    [google_maps] {city}: карточек в ленте {len(hrefs)}")
    for href in hrefs:
        page.goto(href, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(1500, 2500))
        if _is_blocked(page):
            raise SourceBlocked(f"google_maps: блок/капча на карточке, {city}")
        rec = _parse_card(page, city)
        if rec:
            out.append(rec)
        time.sleep(random.uniform(1.2, 2.0))
    return out


def scrape_region(region_cfg, log=print):
    from playwright.sync_api import sync_playwright

    out = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="ru-RU", viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            for city in region_cfg["cities"]:
                log(f"  [google_maps] город: {city}")
                try:
                    out.extend(_search_city(page, city, log))
                except SourceBlocked as e:
                    log(f"  [google_maps] ОСТАНОВКА по блоку: {e}")
                    break
        finally:
            browser.close()
    return out
