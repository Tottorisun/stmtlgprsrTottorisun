# -*- coding: utf-8 -*-
"""
2ГИС: обычный HTTP-запрос через curl_cffi с имперсонацией браузерного
TLS-отпечатка. Ни Playwright, ни браузера не нужно — серверный HTML (SSR)
2gis.ru уже содержит всё состояние страницы в `var initialState = JSON.parse('...')`,
и по поисковой выдаче, и по карточке фирмы.

Прямой вызов catalog.api.2gis.ru с публичным ключом (тем, что использует сам
виджет 2gis.ru) пробовался первым — оба известных публичных ключа отвечают 403
"key is blocked" / "incorrect key" (проверено 26.08.2026). 2ГИС ротирует/блокирует
такие ключи по мере их публичного использования, так что рабочий способ —
запрашивать обычные страницы сайта и читать встроенное состояние.

--- ГЛАВНОЕ: чем на самом деле был "блок 2ГИС" (исправление 29.08.2026) ---

ЧТО ЗДЕСЬ БЫЛО НАПИСАНО РАНЬШЕ (и было НЕВЕРНО): "блок именно по IP/сети
целиком", "нужен другой исходящий IP — это ТРЕБОВАНИЕ", "репутация конкретного
IP у антифрод-системы 2ГИС". Вывод был сделан 27.08.2026 из того, что и
Playwright, и голый `requests` с этой сети получали редирект на
`2gis.ru/museum` → капчу на ПЕРВОМ же запросе, включая голую `2gis.ru/tyumen`
без единого поиска, и одинаково на 2gis.ru, 2gis.kz и 2gis.by.

ЧТО ОКАЗАЛОСЬ НА САМОМ ДЕЛЕ (проверено 29.08.2026, отчёт
AI_CONTEXT/TASKS/SCRAPLING_EVAL.md). Блокировка была по ОТПЕЧАТКУ TLS/HTTP2
(JA3/JA4) HTTP-клиента, а не по IP и не по заголовкам. Три замера подряд,
в одну минуту, с одного и того же IP этой машины:

| Как ходим                                                  | Что вернулось                     |
|------------------------------------------------------------|-----------------------------------|
| `requests`, обычные заголовки                                | `2gis.ru/museum`, 11 КБ           |
| `requests` + полный набор браузерных заголовков (UA, Sec-Ch-Ua, Sec-Fetch-*, Referer) | `2gis.ru/museum`, 4 КБ |
| `curl_cffi` c `impersonate="chrome"`, минимальные заголовки  | `2gis.ru/tyumen`, 566 КБ, `initialState` на месте |

Контроль: сразу ПОСЛЕ успеха обычный `requests` в ту же минуту снова получил
заглушку — значит дело в клиенте, а не в том, что блок "сам отпустил".
Идеальные браузерные заголовки НЕ помогли — значит дело и не в них.

ПОЧЕМУ ОШИБЛИСЬ. Наблюдения были верные (блок с первого запроса, на всех TLD,
и через Playwright, и через requests), а вывод из них — нет. "Одинаково на
всех доменах и с первого запроса" честно исключает домен/TLD и исключает
пагинационный лимит, но НЕ различает IP и клиента: обе гипотезы объясняют
ровно эти симптомы. Проверку, которая их различает (тот же IP, та же минута,
ДРУГОЙ клиент), тогда не сделали — сразу выбрали IP. Playwright не был
контрпримером: браузерная автоматизация оставляет собственные следы и тоже
не проходила, что лишь укрепило неверный вывод.

ПРАКТИЧЕСКИЙ ВЫВОД: прокси, резидентные IP и смена сети ради 2ГИС НЕ НУЖНЫ.
Не покупать. Источник работает с этой самой сети — нужен только клиент,
который здоровается как браузер.

ЧЕГО МЫ НЕ ДЕЛАЕМ: капчу не решаем и не обходим. 2ГИС нам её просто ни разу
не показал — он отдал обычную страницу. Если когда-нибудь покажет (или снова
уведёт на /museum), модуль честно останавливается через SourceBlocked, как и
раньше, а не пытается пройти проверку.

--- Сверка настроек с RUSIMEX/leadgen/Hlebozavody_BY_KZ (27.08.2026, по прямому
запросу владельца) ---

Кроме общей техники (harvest_2gis_v2.py), там нашёлся более свежий и куда
конкретнее задокументированный приём — scripts/templates/2gis_deep_scan.md,
проверенный на 2gis.am/az/tj 18-19.08.2026 (harvest_2gis_v2.py от 6.08 —
на три недели старее). Взято оттуда, с конкретными значениями, и сохранено
при переходе на curl_cffi ниже:

1. РОТАЦИЯ ЗАПРОСА ВМЕСТО ГЛУБОКОЙ ПАГИНАЦИИ ОДНОГО ЗАПРОСА. Симптом блокировки
   по их описанию: `currentPage` зависает на 1 (или прямая капча) после 4-6
   страниц ОДНОГО и того же поискового запроса, держится часами. Каждый НОВЫЙ
   запрос вида /search/<query> даёт свежую страницу 1 — даже если предыдущий
   запрос той же тематики уже заблокирован. У них процент новых карточек по
   мере смены запроса: ~90% на 1-м -> ~65% на 2-м -> ~50% на 3-м, дальше не
   имеет смысла. Раньше здесь был один запрос "стоматология" и до 5 страниц
   вглубь — именно тот паттерн, который у них ловил блокировку. Ниже —
   ротация из 3 запросов, каждый не глубже 2 страниц.
2. ПАУЗА МЕЖДУ ЗАПРОСАМИ КАРТОЧЕК ~2-2.5с — их значение, чтобы не поймать
   отдельный burst-лимит ("no state" при частых запросах подряд, не путать с
   блокировкой пагинации).
3. ЧТЕНИЕ ВСТРОЕННОГО СОСТОЯНИЯ КАРТОЧКИ ВМЕСТО НАВИГАЦИИ. Их рецепт вместо
   page.goto() на каждую карточку забирал HTML карточки и регэкспом доставал
   `var initialState = JSON.parse('...')` из тела ответа. С curl_cffi это
   стало прямым HTTP-запросом (браузер вообще не участвует) — тот же приём,
   на один слой проще.

ЧТО НЕ ПЕРЕНЕСЕНО и почему: их рецепт использует префикс `/ru/` в URL
(`2gis.<TLD>/ru/<city>/firm/<id>`) — но это специфика TLD, где русский язык
не язык по умолчанию (2gis.am/az/tj). У 2gis.ru (наш случай) русский и так
язык по умолчанию, и более старый harvest_2gis_v2.py для 2gis.kz/by/ru тоже
не использует `/ru/` в пути — слепо копировать чужой префикс сюда значило бы
сломать рабочий URL ради приёма, решающего не нашу проблему. Оставлен прежний
путь без `/ru/`.

--- ПЭЙСИНГ НЕ УСКОРЕН ---

curl_cffi быстрее Playwright на порядки, но паузы оставлены ровно прежними:
их значения пришли из месяцев прогонов RUSIMEX и существуют, чтобы не ловить
блокировку на масштабе, а не потому, что транспорт медленный. Быстрее
транспорт — та же вежливость. Прежняя пауза 3.0-4.5с после навигации была
ожиданием отрисовки SPA; при SSR ждать нечего, но интервал между запросами
к поиску сохранён таким же (см. _SEARCH_RENDER_PAUSE ниже), чтобы частота
обращений к 2ГИС не выросла молча вместе со сменой клиента.
"""
import re
import json
import time
import random

from curl_cffi import requests as curl_requests

from .base import SourceBlocked

# Ротация вместо одного запроса + глубокой пагинации (см. docstring, п.1)
QUERIES = ["стоматология", "стоматологическая клиника", "зубной врач"]
MAX_PAGES_PER_QUERY = 2

# Профиль браузера для подделки TLS/HTTP2-отпечатка. Именно это, а не заголовки
# и не IP, открывает 2ГИС (см. docstring). Заголовки поверх профиля НЕ
# добавляем: рабочим на живой проверке был именно вариант "impersonate +
# минимальные заголовки", а свои заголовки поверх имперсонации рискуют
# рассогласовать набор/порядок с тем, что обещает TLS-отпечаток.
IMPERSONATE = "chrome"

TIMEOUT = 45

# Паузы. Значения не менять "потому что стало быстрее" — см. docstring.
_SEARCH_RENDER_PAUSE = (3.0, 4.5)   # бывшее ожидание отрисовки SPA, сохранено как пэйсинг
_SEARCH_PAGE_PAUSE = (1.2, 2.2)     # между страницами одного запроса
_CARD_PAUSE = (2.0, 2.5)            # между карточками (2gis_deep_scan.md, п.2)

_STATE_MARKER = "var initialState = JSON.parse('"
_FIRM_ID_RE = re.compile(r"/firm/(\d+)")

# Признаки того, что нас увели с настоящей страницы. /museum — страница-заглушка,
# на которую 2ГИС редиректит подозрительного клиента; captcha.2gis.ru — сама
# проверка. Ни то, ни другое не обходим: останавливаемся (см. base.SourceBlocked).
_BLOCK_MARKERS = ("captcha.2gis.ru", "/museum")


def _js_unescape(lit):
    """Раскодировать тело JS-строки в одинарных кавычках.

    В SSR-HTML состояние лежит как `JSON.parse('<JSON, экранированный под
    JS-строку>')`. Браузер раньше делал это сам (new Function(...)); здесь
    нужен один проход, снимающий ровно ОДИН слой экранирования — после него
    остаётся валидный JSON (внутренние `\\"` JSON'а приходят как `\\\\"` и
    корректно превращаются обратно в `\\"`, потому что `\\\\` обрабатывается
    раньше следующей кавычки при проходе слева направо).
    """
    out = []
    i = 0
    n = len(lit)
    simple = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
    while i < n:
        c = lit[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        e = lit[i]
        if e == "u":
            out.append(chr(int(lit[i + 1:i + 5], 16)))
            i += 5
        elif e == "x":
            out.append(chr(int(lit[i + 1:i + 3], 16)))
            i += 3
        elif e in simple:
            out.append(simple[e])
            i += 1
        else:
            # \' \" \\ \/ и всё прочее — сам символ
            out.append(e)
            i += 1
    return "".join(out)


def _extract_state(html):
    """Достать и разобрать `var initialState = JSON.parse('...')`.
    None, если маркера нет или JSON не разобрался (порча/подменённая страница)."""
    start = html.find(_STATE_MARKER)
    if start < 0:
        return None
    i = start + len(_STATE_MARKER)
    while i < len(html):
        if html[i] == "\\":
            i += 2
            continue
        if html[i] == "'":
            break
        i += 1
    try:
        return json.loads(_js_unescape(html[start + len(_STATE_MARKER):i]))
    except ValueError:
        return None


def _get(session, url):
    """Один GET с проверкой на блок. Возвращает HTML-текст.

    2ГИС отдаёт UTF-8; декодируем явно, а не полагаемся на угадывание
    кодировки — детерминированный результат важнее, а ошибиться тут значит
    получить мусор в названиях/адресах, который дальше тихо уедет в базу."""
    r = session.get(url, timeout=TIMEOUT)
    final_url = r.url or url
    if any(m in final_url for m in _BLOCK_MARKERS):
        raise SourceBlocked(f"2gis: капча/блок на {final_url[:160]}")
    if r.status_code in (403, 429):
        raise SourceBlocked(f"2gis: HTTP {r.status_code} на {final_url[:160]}")
    if r.status_code != 200:
        return ""
    return r.content.decode("utf-8", "replace")


def open_session(log=print):
    """Одна curl_cffi-сессия на весь регион (интерфейс общий с остальными
    источниками, см. src/pipeline.py). Сессия держит куки и переиспользует
    соединение; браузер, в отличие от прежней версии, не запускается вовсе."""
    return curl_requests.Session(impersonate=IMPERSONATE)


def close_session(session):
    session.close()


def _collect_firm_ids(session, city_slug, seen_ids):
    """Ротация по QUERIES, максимум MAX_PAGES_PER_QUERY страниц на запрос
    (см. docstring, п.1 — глубже означает риск поймать блокировку пагинации).

    Идентификаторы берём регэкспом по ссылкам /firm/<id> в SSR-HTML — это ровно
    то, что раньше собиралось из DOM (`a[href*='/firm/']`), только без браузера.
    Намеренно НЕ берём ключи data.entity.profile из initialState: туда попадают
    и рекламные врезки, которых нет в самой выдаче."""
    new_ids = []
    for query in QUERIES:
        for page_n in range(1, MAX_PAGES_PER_QUERY + 1):
            url = f"https://2gis.ru/{city_slug}/search/{query}"
            if page_n > 1:
                url += f"/page/{page_n}"
            html = _get(session, url)
            time.sleep(random.uniform(*_SEARCH_RENDER_PAUSE))
            page_new = 0
            for fid in _FIRM_ID_RE.findall(html):
                if fid not in seen_ids:
                    seen_ids.add(fid)
                    new_ids.append(fid)
                    page_new += 1
            if page_new == 0:
                break
            time.sleep(random.uniform(*_SEARCH_PAGE_PAUSE))
        # 0 новых на первой странице -> либо тема пуста в этом городе, либо
        # именно этот запрос уже заблокирован вглубь — следующий запрос в
        # ротации всё равно пробуем, он даёт свежую страницу 1 (docstring, п.1)
    return new_ids


def _pick_contacts(item):
    """Телефоны/почты/сайт из contact_groups карточки.

    Про сайт важно: у контакта type=website поле `value` — это ТРЕКОВЫЙ
    редирект 2ГИС (`link.2gis.ru/1.2/.../?http://настоящий-сайт`), а настоящий
    адрес лежит в `url`. Поэтому для сайта порядок предпочтения обратный
    (url раньше value): в режиме has-site этот URL уходит в лид и дальше в
    audit_sites.py — записать туда link.2gis.ru значило бы проверять на
    соответствие закону сам 2ГИС вместо клиники. Для phone/email `value`,
    наоборот, самое чистое (нормализованный номер / адрес почты).

    Оговорка на будущее: website-контактов у карточки бывает несколько, и
    вторым нередко идёт виджет онлайн-чата (видели jivo.chat). Берём первый —
    2ГИС сам ставит основной сайт первым; если однажды попадёт чат, это будет
    видно в аудите сайта, а не тихо испортит данные."""
    phones, emails, website = [], [], ""
    for g in item.get("contact_groups") or []:
        for c in g.get("contacts") or []:
            t = c.get("type")
            if t == "phone":
                v = c.get("value") or c.get("text")
                if v:
                    phones.append(v)
            elif t == "email":
                v = c.get("value") or c.get("text")
                if v:
                    emails.append(v)
            elif t == "website" and not website:
                v = c.get("url") or c.get("value") or c.get("text")
                if v:
                    website = str(v).strip()
    return phones, emails, website


def _scrape_card(session, city_slug, fid):
    """Забрать карточку фирмы обычным GET и разобрать встроенное состояние
    (приём из 2gis_deep_scan.md, см. docstring, п.3 — только теперь без
    браузера в качестве посредника)."""
    url = f"https://2gis.ru/{city_slug}/firm/{fid}"
    html = _get(session, url)
    if not html:
        return None
    state = _extract_state(html)
    if not state:
        return None
    try:
        profile = state["data"]["entity"]["profile"]
    except (KeyError, TypeError):
        return None
    item = (profile.get(fid) or {}).get("data")
    if not item:
        for v in profile.values():
            d = (v or {}).get("data")
            if d and str(d.get("id", "")).startswith(fid):
                item = d
                break
    if not item:
        return None
    phones, emails, website = _pick_contacts(item)
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
        "has_website": bool(website),
        "website": website,
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


def scrape_city(session, city, log=print):
    """session — из open_session() (curl_cffi.requests.Session с имперсонацией).
    Кидает SourceBlocked при капче/блоке — вызывающий код (pipeline.py) это ловит
    и исключает 2gis из дальнейших городов региона, а не роняет весь прогон."""
    slug = _slugify_city(city)
    log(f"  [2gis] город: {city} (slug={slug})")
    seen_ids = set()
    ids = _collect_firm_ids(session, slug, seen_ids)
    log(f"  [2gis] {city}: найдено карточек {len(ids)}")
    out = []
    for fid in ids:
        rec = _scrape_card(session, slug, fid)
        if rec:
            out.append(rec)
        # 2.0-2.5с — значение из 2gis_deep_scan.md (см. docstring, п.2).
        # НЕ ускорять из-за того, что транспорт стал быстрее.
        time.sleep(random.uniform(*_CARD_PAUSE))
    return out


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
                log(f"  [2gis] ОСТАНОВКА по блоку: {e}")
                break
    finally:
        close_session(session)
    return out
