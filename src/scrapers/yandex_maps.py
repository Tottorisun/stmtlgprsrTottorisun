# -*- coding: utf-8 -*-
"""
Яндекс.Карты: серверный HTML (SSR) уже содержит JSON с результатами поиска
в <script type="application/json" class="state-view">. Ключ/API не нужен,
браузер не нужен — обычный requests.get(). Техника подтверждена рабочей на
живых данных 26.08.2026 (см. отчёт агента): поиск "стоматология Тюмень" вернул
реальные карточки с телефонами, адресами и полем urls (сайт) при наличии.

Метод и структура состояния позаимствованы (и упрощены под один регион/страну)
из D:\\Мои разработки\\RUSIMEX\\leadgen\\Hlebozavody_BY_KZ\\scripts\\harvest_yandex.py —
там тот же приём проверен на тысячах запросов по 2ГИС/Яндексу.

Пэйсинг (27.08.2026, по прямому запросу владельца сверить настройки, а не только
приём): у harvest_yandex.py задержки между страницами 4.0-6.5с и между запросами
3.5-5.5с — примерно вдвое медленнее, чем было здесь до этой правки (1.3-2.4с /
1.5-2.8с). Мой прогон по Тюменской области (283 записи, один регион) на быстром
пэйсинге прошёл без капчи, но это далеко не тот масштаб, на котором проверен
harvest_yandex.py (10 стран, десятки городов, месяцы прогонов, без единого
упоминания капчи по Яндексу в QUALITY_RULES.md). Прежде чем гнать Москву
(на порядок больше карточек за один запуск, чем Тюмень) — беру их значения, а
не жду, пока капча найдётся на практике первой. Глубину пагинации (8 страниц
вместо их 4) НЕ уменьшаю: собственный живой тест 26.08.2026 показал, что
Яндекс по "стоматология Тюмень" продолжал отдавать новые id и на 5-й странице —
урезать до 4 значило бы терять реальные данные без доказанной необходимости
именно для этого пункта.

Устойчивость к сбоям: harvest_yandex.py оборачивает обработку каждого города в
try/except и переходит к следующему, а не роняет весь прогон — здесь раньше
такой защиты не было (необработанное исключение в _fetch_page положило бы
весь main.py). Добавлено ниже в scrape_region().

Замечание по гео: с сети, где сейчас работает эта машина (не российский IP),
yandex.ru молча редиректит на yandex.com/maps, но при этом всё равно отдаёт
российскую выдачу (see report) — Accept-Language: ru-RU этого достаточно.
Если запускать с российского IP, редиректа не будет — код одинаково работает
в обоих случаях, редирект не разрывает сессию (requests сам идёт за Location).
"""
import re
import json
import time
import random

import requests

from .base import SourceBlocked

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

QUERIES = ["стоматология", "стоматологическая клиника", "детская стоматология", "зубной врач"]
MAX_PAGES_PER_QUERY = 8
STOP_AFTER_EMPTY_PAGES = 2   # если 2 страницы подряд не дали новых id — хватит

STATE_VIEW_RE = re.compile(r'<script type="application/json" class="state-view">(.*?)</script>', re.S)


def _session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })
    return s


def _fetch_page(sess, query, page):
    url = "https://yandex.ru/maps/?text=" + requests.utils.quote(query)
    if page > 1:
        url += f"&page={page}"
    r = sess.get(url, timeout=40)
    if r.status_code in (403, 429):
        raise SourceBlocked(f"yandex_maps: HTTP {r.status_code} на {url[:100]}")
    if "showcaptcha" in r.url or "SmartCaptcha" in r.text[:6000]:
        raise SourceBlocked(f"yandex_maps: капча на {r.url[:120]}")
    m = STATE_VIEW_RE.search(r.text)
    if not m:
        return None
    data = json.loads(m.group(1))
    for frame in data.get("stack", []):
        if isinstance(frame, dict) and "results" in frame:
            return frame["results"]
    return None


def _extract(item, city, source_url):
    if not item.get("id"):
        return None  # редакционные вставки в выдаче ("Стоматологии в ... : обзор") — не организация
    cats = [c.get("name") for c in (item.get("categories") or []) if c.get("name")]
    urls = item.get("urls") or []
    phones = [p.get("value") or p.get("number") for p in (item.get("phones") or [])]
    return {
        "name": item.get("shortTitle") or item.get("title") or "",
        "categories": cats,
        "city": city,
        "address": item.get("fullAddress") or "",
        "phones_raw": [p for p in phones if p],
        "emails_raw": [],  # Яндекс.Карты email в карточке организации не публикует
        "has_website": bool(urls and urls[0]),
        "source": "yandex_maps",
        "source_url": source_url,
    }


def scrape_city(city, log=print):
    """Собрать по одному городу все записи по всем запросам QUERIES. Возвращает
    список сырых записей (см. base.RAW_FIELDS), включая записи С сайтом —
    фильтрацию has_website делает pipeline.py, здесь только сбор."""
    sess = _session()
    out = []
    seen_ids = set()
    for q in QUERIES:
        full_q = f"{q} {city}"
        new_in_query = 0
        empty_streak = 0
        for page in range(1, MAX_PAGES_PER_QUERY + 1):
            res = _fetch_page(sess, full_q, page)
            items = (res or {}).get("items", [])
            if not items:
                break
            page_new = 0
            for it in items:
                oid = it.get("id")
                if not oid or oid in seen_ids:
                    continue
                seen_ids.add(oid)
                page_new += 1
                rec = _extract(it, city, f"https://yandex.ru/maps/org/{it.get('seoname','org')}/{oid}/")
                if rec:
                    out.append(rec)
            empty_streak = empty_streak + 1 if page_new == 0 else 0
            if empty_streak >= STOP_AFTER_EMPTY_PAGES:
                break
            new_in_query += page_new
            # 4.0-6.5с — значение из harvest_yandex.py, проверено на масштабе
            # на порядки больше нашего (см. docstring модуля)
            time.sleep(random.uniform(4.0, 6.5))
        log(f"    [yandex_maps] {city} / {q!r}: новых карточек {new_in_query}")
        time.sleep(random.uniform(3.5, 5.5))
    return out


def scrape_region(region_cfg, log=print):
    """region_cfg: элемент config.regions.REGIONS (dict с ключом 'cities')."""
    out = []
    for city in region_cfg["cities"]:
        log(f"  [yandex_maps] город: {city}")
        try:
            out.extend(scrape_city(city, log=log))
        except SourceBlocked as e:
            log(f"  [yandex_maps] ОСТАНОВКА по блоку: {e}")
            break
        except Exception as e:
            # Как в harvest_yandex.py: сбой одного города (сеть/парсинг) не должен
            # ронять весь регион — логируем и идём дальше, а не падаем молча.
            log(f"  [yandex_maps] ОШИБКА в городе {city}, пропускаю: "
                f"{type(e).__name__}: {str(e)[:160]}")
            continue
    return out
