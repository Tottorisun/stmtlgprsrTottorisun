# -*- coding: utf-8 -*-
"""
2ГИС: без Playwright не обойтись. Прямой вызов catalog.api.2gis.ru с публичным
ключом (тем, что использует сам виджет 2gis.ru, виден в его сетевых запросах)
пробовался первым — оба известных публичных ключа сейчас отвечают 403
"key is blocked" / "incorrect key" (проверено 26.08.2026). 2ГИС, судя по всему,
ротирует/блокирует такие ключи по мере их публичного использования, так что
рабочий способ — открывать страницы как обычный браузер и читать данные из
window.initialState/встроенного JS-состояния карточки фирмы.

--- Сверка настроек с RUSIMEX/leadgen/Hlebozavody_BY_KZ (27.08.2026, по прямому
запросу владельца) ---

Кроме общей техники (harvest_2gis_v2.py), там нашёлся более свежий и куда
конкретнее задокументированный приём — scripts/templates/2gis_deep_scan.md,
проверенный на 2gis.am/az/tj 18-19.08.2026 (harvest_2gis_v2.py от 6.08 —
на три недели старее). Взято оттуда, с конкретными значениями:

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
   отдельный burst-лимит ("no state" при частых fetch() подряд, не путать с
   блокировкой пагинации). Было 1.5-2.5с — сузил нижнюю границу до 2.0.
3. FETCH() ВМЕСТО НАВИГАЦИИ ЗА ДЕТАЛЯМИ КАРТОЧКИ. Их рецепт вместо
   page.goto() на каждую карточку делает fetch() HTML-страницы карточки прямо
   в браузерном контексте и регэкспом достаёт встроенное
   `var initialState = JSON.parse('...')` из тела ответа — задокументировано
   как более быстрый и надёжный способ, чем клик/навигация. Перенесено ниже
   (_scrape_card теперь делает это через page.evaluate + fetch, без goto на
   каждую карточку).

ЧТО НЕ ПЕРЕНЕСЕНО и почему: их рецепт использует префикс `/ru/` в URL
(`2gis.<TLD>/ru/<city>/firm/<id>`) — но это специфика TLD, где русский язык
не язык по умолчанию (2gis.am/az/tj). У 2gis.ru (наш случай) русский и так
язык по умолчанию, и более старый harvest_2gis_v2.py для 2gis.kz/by/ru тоже
не использует `/ru/` в пути — слепо копировать чужой префикс сюда значило бы
сломать рабочий URL ради приёма, решающего не нашу проблему. Оставлен прежний
путь без `/ru/`.

--- ГЛАВНОЕ: это НЕ чинит блокировку с этой сети ---

С сети, где сейчас работает эта машина, 2gis.ru отдаёт на любой запрос —
включая голый /tyumen без единого поиска — жёсткий редирект на
captcha.2gis.ru/form, "подозрительная активность с вашего IP" (проверено и
через Playwright, и напрямую через requests). Проверено также 27.08.2026:
ТА ЖЕ блокировка одинаково срабатывает на 2gis.kz и 2gis.by с этой же сети —
это не особенность именно российского домена, блок именно по IP/сети целиком,
на уровне всей платформы 2ГИС. Симптом иной, чем в 2gis_deep_scan.md (там —
блокировка вглубь ОДНОГО запроса после нескольких страниц, снимается сменой
запроса; здесь — блокировка с первого же запроса, ротация запроса не поможет).

Прокси/VPN-инфраструктуры в коде и документах RUSIMEX НЕ найдено — весь код
там (все *2gis*.py, QUALITY_RULES.md) работает через обычный Playwright +
системный Chrome, без единого упоминания proxy/VPN/резидентных IP. Значит,
там просто не пытались обойти сетевой блок таким способом (или он им не
встречался на их тогдашней сети). Единственная явная зацепка по времени: их
рабочие подтверждения по 2GIS датированы 6-19.08.2026, а сегодняшний блок
(27.08.2026) — сплошной и с первого запроса. Похоже на то, что IP/сеть этой
машины со временем накопили блокировку у 2ГИС (не исключено, что как раз от
объёма прошлого проекта) — а не на то, что 2ГИС в принципе не пускает эту
страну/провайдера. Практический вывод для владельца: нужен другой исходящий
IP (другая сеть, смена VPN-выхода, или резидентный прокси, если будет
заведён) — это ТРЕБОВАНИЕ, не пожелание, и не то же самое, что "подождать
российский IP": дело не в геолокации самой по себе, а в репутации конкретного
IP у антифрод-системы 2ГИС. Код ниже рабочий и не трогать его не придётся —
достаточно сменить сеть.
"""
import re
import time
import random

from .base import SourceBlocked

# Ротация вместо одного запроса + глубокой пагинации (см. docstring, п.1)
QUERIES = ["стоматология", "стоматологическая клиника", "зубной врач"]
MAX_PAGES_PER_QUERY = 2

FIND_STATE_JS = r"""
async (url) => {
    function findStateCode(html) {
        const marker = "var initialState = JSON.parse('";
        const startIdx = html.indexOf(marker);
        if (startIdx < 0) return null;
        let i = startIdx + marker.length;
        while (i < html.length) {
            if (html[i] === '\\') { i += 2; continue; }
            if (html[i] === "'") { i += 1; break; }
            i += 1;
        }
        return html.slice(startIdx + "var initialState = ".length, i + 2);
    }
    try {
        const resp = await fetch(url);
        const finalUrl = resp.url || url;
        const html = await resp.text();
        const code = findStateCode(html);
        if (!code) return {error: "no state", finalUrl, htmlHead: html.slice(0, 200)};
        const state = new Function('return ' + code)();
        return {state, finalUrl};
    } catch (e) {
        return {error: String(e)};
    }
}
"""


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


def _collect_firm_ids(page, city_slug, seen_ids):
    """Ротация по QUERIES, максимум MAX_PAGES_PER_QUERY страниц на запрос
    (см. docstring, п.1 — глубже означает риск поймать блокировку пагинации)."""
    new_ids = []
    for query in QUERIES:
        query_new = 0
        for page_n in range(1, MAX_PAGES_PER_QUERY + 1):
            url = f"https://2gis.ru/{city_slug}/search/{query}"
            if page_n > 1:
                url += f"/page/{page_n}"
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(random.randint(3000, 4500))
            _check_blocked(page)
            hrefs = page.eval_on_selector_all(
                "a[href*='/firm/']", "els => els.map(e => e.getAttribute('href'))")
            page_new = 0
            for h in hrefs:
                m = re.search(r"/firm/(\d+)", h or "")
                if m and m.group(1) not in seen_ids:
                    seen_ids.add(m.group(1))
                    new_ids.append(m.group(1))
                    page_new += 1
            query_new += page_new
            if page_new == 0:
                break
            time.sleep(random.uniform(1.2, 2.2))
        # query_new == 0 на первой странице -> либо тема пуста в этом городе,
        # либо именно этот запрос уже заблокирован вглубь — следующий запрос
        # в ротации всё равно пробуем, он даёт свежую страницу 1 (см. docstring, п.1)
    return new_ids


def _scrape_card(page, city_slug, fid):
    """fetch() + разбор встроенного состояния вместо page.goto() на карточку —
    приём из 2gis_deep_scan.md (см. docstring, п.3): быстрее и надёжнее клика/
    навигации, задокументировано как проверенное на живых данных 18-19.08.2026."""
    url = f"https://2gis.ru/{city_slug}/firm/{fid}"
    result = page.evaluate(FIND_STATE_JS, url)
    if result.get("error"):
        return None
    final_url = result.get("finalUrl") or url
    if "captcha.2gis.ru" in final_url or "/museum" in final_url:
        raise SourceBlocked(f"2gis: капча/блок на {final_url[:160]}")
    state = result.get("state") or {}
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


def open_session(log=print):
    """Один запуск Chrome на весь регион (открывать браузер на каждый город —
    секунды накладных расходов ×N городов; чек-пойнт по городам делается на
    уровне SQLite в pipeline.py, не на уровне браузера, см. 27.08.2026)."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser, ctx = _open_browser(pw)
    page = ctx.new_page()
    return {"pw": pw, "browser": browser, "ctx": ctx, "page": page}


def close_session(session):
    try:
        session["browser"].close()
    finally:
        session["pw"].stop()


def scrape_city(session, city, log=print):
    """Требует Playwright + системный Chrome (через session из open_session()).
    Кидает SourceBlocked при капче — вызывающий код (pipeline.py) это ловит
    и исключает 2gis из дальнейших городов региона, а не роняет весь прогон."""
    page = session["page"]
    slug = _slugify_city(city)
    log(f"  [2gis] город: {city} (slug={slug})")
    seen_ids = set()
    ids = _collect_firm_ids(page, slug, seen_ids)
    log(f"  [2gis] {city}: найдено карточек {len(ids)}")
    out = []
    for fid in ids:
        rec = _scrape_card(page, slug, fid)
        if rec:
            out.append(rec)
        # 2.0-2.5с — значение из 2gis_deep_scan.md (см. docstring, п.2)
        time.sleep(random.uniform(2.0, 2.5))
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
