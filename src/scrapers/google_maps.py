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

--- Сверка настроек с RUSIMEX (27.08.2026, по прямому запросу владельца) ---
Сравнено с gmaps_kz_harvest.py / harvest_gmaps_by_kz.py / harvest_gmaps_uz_az.py /
gmaps_az.py / gmaps_uz_az_cards.py — все пять используют один и тот же набор
настроек (не разнобой, значит проверено многократно на разных странах/сессиях),
взято отсюда:

1. `launch_persistent_context` с сохраняемым профилем вместо
   `browser.new_context()` "с нуля" каждый запуск — второе было здесь раньше.
   Постоянный профиль копит cookies/consent/отпечаток браузера между
   запусками, а не выглядит как "новый браузер" при каждом старте — это
   явно предпочтённый вариант во всех пяти их скриптах, не единичный случай.
2. Флаг запуска `--lang=ru` ДОПОЛНИТЕЛЬНО к `locale="ru-RU"` контекста —
   у них оба сразу, здесь раньше был только locale.
3. Капча/блок — 3 попытки с бэкоффом (задержка `30 * номер_попытки` секунд)
   перед тем, как считать источник заблокированным, а не разовая проверка ->
   сразу SourceBlocked (как было здесь). У них так на КАЖДОЙ навигации
   (и поиск, и карточка) — часть блоков временная и снимается сама.
4. Пэйсинг между навигациями 2.0-4.0с (было 1.2-2.5с/1.4-2.2с — заметно
   быстрее). Их значение проверено на 8 странах СНГ за недели прогонов;
   моё — только на одном городе одного региона. Беру их цифру, а не жду,
   пока при масштабировании на всю Тюменскую область/Москву найдётся
   лимит, которого не было видно на одном городе.
5. Прокрутка ленты: у них `max_rounds=40`, порог стабильности 4 (было 25/3).
   Беру их значения — глубже прокрутка стоит немного лишнего времени, а не
   недобора карточек на больших городах (Москва даст куда больше карточек
   в ленте, чем Тюмень, на которой изначально подобраны 25/3).

ЧТО НЕ ПЕРЕНЕСЕНО: их двухфазная схема (сначала собрать индекс всех карточек
по всем городам/запросам, потом отдельно обойти карточки, с чекпоинтами на
диск после каждых 10) — это инфраструктура для многодневных прогонов по 8+
странам с возможностью прерывания. Для одного запуска на один регион это
лишняя сложность без выигрыша: наш прогон занимает минуты, не дни, а
чекпоинт на диск между городами уже даёт pipeline.py (см. src/pipeline.py) —
досрочно перенос их checkpoint-файлов сюда не даёт ничего, чего пайплайн не
делает иначе.
"""
import re
import time
import random
import pathlib
from urllib.parse import quote

from .base import SourceBlocked

SEARCH_QUERY = "стоматология"
MAX_SCROLLS = 40           # RUSIMEX-значение, см. docstring п.5
STABLE_ROUNDS = 4          # RUSIMEX-значение, см. docstring п.5
MAX_RESULTS_PER_CITY = 80

PROFILE_DIR = pathlib.Path(__file__).resolve().parents[2] / "data" / "_gmaps_profile"


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


def _goto_with_retry(page, url, what, log):
    """3 попытки с бэкоффом 30*попытка секунд перед выводом о реальном блоке —
    приём из harvest_gmaps_uz_az.py/gmaps_uz_az_cards.py (см. docstring п.3)."""
    for attempt in range(1, 4):
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
        except Exception:
            if attempt == 3:
                raise SourceBlocked(f"google_maps: не открылось за 3 попытки ({what})")
            time.sleep(30 * attempt)
            continue
        page.wait_for_timeout(1200)
        _handle_consent(page)
        if not _is_blocked(page):
            return
        log(f"    [google_maps] капча/блок, попытка {attempt}/3 ({what})")
        if attempt < 3:
            time.sleep(30 * attempt)
    raise SourceBlocked(f"google_maps: капча/блок держится 3 попытки подряд ({what})")


def _scroll_feed(page):
    last, stable = -1, 0
    for _ in range(MAX_SCROLLS):
        try:
            page.locator("div[role='feed']").evaluate("el => el.scrollBy(0, el.scrollHeight)")
        except Exception:
            break
        time.sleep(random.uniform(1.5, 2.4))
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
            if stable >= STABLE_ROUNDS:
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
    # Часть карточек на Google Maps использует поле "название" как SEO-текст:
    # "Абсолют-Дент | Стоматология Тюмень | Детский стоматолог, брекеты,
    # имплантация" вместо просто "Абсолют-Дент" (реальный случай, найден при
    # проверке слияния с Яндекс.Картами 26.08.2026 -- без этой чистки такая
    # запись не совпадает по имени ни с одним источником и остаётся дублем).
    # Настоящее название организации почти никогда не содержит " | " —
    # берём то, что до первого разделителя.
    if " | " in name:
        name = name.split(" | ", 1)[0].strip()

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
    _goto_with_retry(page, url, f"поиск {city}", log)

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
        _goto_with_retry(page, href, f"карточка в {city}", log)
        rec = _parse_card(page, city)
        if rec:
            out.append(rec)
        # 2.0-4.0с — значение из RUSIMEX-скриптов, см. docstring п.4
        time.sleep(random.uniform(2.0, 4.0))
    return out


def open_session(log=print):
    """Один запуск persistent-context Chrome на весь регион (запуск браузера —
    секунды накладных расходов ×N городов; чек-пойнт по городам делается на
    уровне SQLite в pipeline.py, не на уровне браузера, см. 27.08.2026)."""
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    # launch_persistent_context вместо launch()+new_context() — см. docstring п.1
    ctx = pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        channel="chrome", headless=False,
        args=["--disable-blink-features=AutomationControlled", "--disable-quic",
              "--window-position=2000,2000", "--lang=ru"],  # --lang=ru: см. docstring п.2
        locale="ru-RU", viewport={"width": 1440, "height": 900},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return {"pw": pw, "ctx": ctx, "page": page}


def close_session(session):
    try:
        session["ctx"].close()
    finally:
        session["pw"].stop()


def scrape_city(session, city, log=print):
    """session — из open_session(). Кидает SourceBlocked при устойчивой капче —
    вызывающий код (pipeline.py) это ловит и исключает google_maps из
    дальнейших городов региона, а не роняет весь прогон."""
    log(f"  [google_maps] город: {city}")
    return _search_city(session["page"], city, log)


def scrape_region(region_cfg, log=print):
    """Оставлено для прямого вызова одного источника целиком (напр. из тестов/
    консоли) — собирает весь регион в памяти и возвращает одним списком, БЕЗ
    промежуточных сохранений. src/pipeline.py с 27.08.2026 использует
    open_session/scrape_city/close_session напрямую для чек-пойнта в SQLite
    после каждого города — см. src/pipeline.py."""
    session = open_session(log=log)
    out = []
    try:
        for city in region_cfg["cities"]:
            try:
                out.extend(scrape_city(session, city, log=log))
            except SourceBlocked as e:
                log(f"  [google_maps] ОСТАНОВКА по блоку: {e}")
                break
    finally:
        close_session(session)
    return out
