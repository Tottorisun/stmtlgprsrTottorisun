# -*- coding: utf-8 -*-
"""
Аудит сайта стоматологии на соответствие обязательному составу информации по
закону (разворот 28.08.2026 — см. README и outreach/LEGAL_BASIS.md).

ЧТО ЭТО ДАЁТ. Для клиники, у которой сайт ЕСТЬ (режим has-site в src/pipeline.py),
проверяем эвристиками, есть ли на сайте каждый обязательный по закону элемент:
политика ПДн, согласие на обработку ПДн у формы, cookie-баннер, версия для
слабовидящих, реквизиты лицензии, цены на услуги, сведения о врачах. Результат —
список ВЕРОЯТНО ОТСУТСТВУЮЩИХ элементов: это и есть предметный повод для письма
(«на сайте не нашли политику ПДн, версию для слабовидящих и куки-баннер — по
закону они обязательны, поможем закрыть»).

ЧЕСТНОСТЬ (критично, тот же урок, что и с ~60-70% ложных «нет сайта» в прошлой
итерации проекта). Внешняя эвристика по HTML ошибается в ОБЕ стороны, и особенно
опасны ложные «отсутствует»:
  * элемент может быть, но не там, куда мы смотрели (другая страница, футер,
    подгружаемый по клику блок);
  * элемент может подгружаться JavaScript'ом уже ПОСЛЕ отдачи HTML (куки-баннеры
    и виджеты «для слабовидящих» — сплошь и рядом именно так) — тогда в исходном
    HTML его нет, а на живом сайте он есть.
Поэтому каждый вывод «отсутствует» — это ГИПОТЕЗА, помечается как
«вероятно отсутствует — проверить вручную», и НИКОГДА не подаётся как факт.
Детектор смещён в сторону «present»: при любом разумном сигнале считаем элемент
присутствующим — чтобы не раздувать список «отсутствует» ложными срабатываниями
(лучше пропустить реальный пробел, чем обвинить клинику в несуществующем).
Отдельно: cookie-баннер и версия для слабовидящих детектируются по статике хуже
всего (чаще всего это JS) — по ним доля ложных «отсутствует» заведомо выше, это
подписано в CHECKS[...]["caveat"] и попадает в отчёт.

СЕТЕВАЯ ДИСЦИПЛИНА. На ОДИН домен максимум 3 запроса (главная + 1 страница-
политика + 1 страница-цены, если нашли на главной ссылки) — ровно те страницы,
что открыл бы живой посетитель, с коротким таймаутом и паузой между ними. Это и
есть «разумное отношение к robots»: при таком потолке задолбить сайт физически
нельзя, поэтому отдельного запроса robots.txt (он добавлял до +10с латентности
на домен при копеечной пользе на нашем объёме) нет. Никакого браузера, никакого
обхода — обычный requests. Чек-пойнт (запись в БД после КАЖДОЙ клиники) делает
вызывающий audit_sites.py, здесь только сама проверка одного сайта.
"""
import re
import time
import json
import random
from urllib.parse import urlparse, urljoin, urldefrag

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
# Таймауты requests (connect, read) НЕ ограничивают ОБЩЕЕ время загрузки: сервер,
# отдающий тело медленным ручейком (никогда не молчит дольше read-таймаута),
# может тянуть страницу десятки секунд. Поэтому тело читаем чанками с ЖЁСТКИМ
# дедлайном по общему времени (MAX_TOTAL_SEC) — это и держит прогон предсказуемым.
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 8
MAX_TOTAL_SEC = 12          # потолок общего времени на одну страницу (вкл. медленный ручеёк)
MAX_HTML_BYTES = 3_000_000  # не тянуть гигантские страницы целиком

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_A_RE = re.compile(r'<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------- проверки
#
# Каждая проверка ищет ПРИЗНАКИ ПРИСУТСТВИЯ элемента. present=True если сработал
# хоть один паттерн (в тексте, в разметке-маркере или в ссылке). Отсутствие
# признака -> present=False -> в отчёте «вероятно отсутствует».
#
# text_patterns  — по видимому тексту (script/style вырезаны, теги сняты);
# markup_patterns — по сырому HTML в нижнем регистре (классы/скрипты/атрибуты
#                   виджетов, которые в видимый текст не попадают);
# link_patterns  — по href или тексту ссылок (для разделов-страниц).
CHECKS = {
    "privacy_policy": {
        "label": "Политика обработки персональных данных",
        "law": "152-ФЗ ст. 18.1 (документ о политике обработки ПДн на сайте)",
        "text_patterns": [
            r"политик\w*\s+(?:в\s+отношении\s+)?обработк\w*\s+персональн",
            r"обработк\w*\s+персональных\s+данных",
            r"конфиденциальн",
        ],
        "markup_patterns": [r"privacy", r"policy", r"personal[-_ ]?data"],
        "link_patterns": [r"политик", r"конфиденциальн", r"персональн", r"privacy", r"policy"],
        "caveat": "",
    },
    "pdn_consent": {
        "label": "Согласие на обработку ПДн у формы записи/обратной связи",
        "law": "152-ФЗ ст. 9 (согласие субъекта на обработку ПДн)",
        "text_patterns": [
            r"согласи\w*\s+на\s+обработк",
            r"да[юё]\s+(?:сво[ёе]\s+)?согласи",
            r"соглас\w*\s+на\s+обработк\w*\s+персональн",
            r"принима\w*[^.]{0,40}политик",
            r"нажимая[^.]{0,60}соглас",
        ],
        "markup_patterns": [r"agree", r"consent", r"personal[-_ ]?data[-_ ]?agreement"],
        "link_patterns": [],
        "caveat": "Согласие часто оформлено чекбоксом/текстом под формой, "
                  "который подгружается JS — детект по статике занижен.",
    },
    "cookie_banner": {
        "label": "Cookie-баннер / уведомление о cookie",
        "law": "152-ФЗ + практика РКН (информирование о cookie и согласие)",
        "text_patterns": [
            r"файл\w*\s+cookie", r"использ\w*\s+cookie", r"использ\w*\s+куки",
            r"cookie[-\s]?файл", r"мы\s+используем\s+куки",
        ],
        "markup_patterns": [
            r"cookie", r"куки", r"cookieconsent", r"cookie[-_]consent",
            r"cookiebot", r"cookie[-_]law", r"gdpr",
        ],
        "link_patterns": [r"cookie", r"куки"],
        "caveat": "Куки-баннеры почти всегда инъектятся JavaScript'ом уже после "
                  "отдачи HTML — по статике доля ложных «отсутствует» высокая.",
    },
    "accessibility": {
        "label": "Версия сайта для слабовидящих",
        "law": "Приказ Минздрава 118н (обязательна версия для слабовидящих)",
        "text_patterns": [
            r"слабовидящ", r"для\s+слабовидящих", r"версия\s+для\s+слабовидящих",
            r"специальн\w*\s+версия", r"для\s+людей\s+с\s+нарушени\w*\s+зрения",
        ],
        "markup_patterns": [
            r"\bbvi\b", r"bvi[-_]", r"специальн\w*версия", r"special[-_]?version",
            r"visually[-_]?impaired", r"accessibility[-_]?widget",
            r"class=[\"'][^\"']*\b(?:bvi|special|eye|blind)\b",
        ],
        "link_patterns": [r"слабовидящ", r"специальн\w*\s*верси", r"версия\s*для"],
        "caveat": "Виджет «для слабовидящих» (bvi.ru и аналоги) обычно грузится "
                  "скриптом — по статике доля ложных «отсутствует» высокая.",
    },
    "license": {
        "label": "Реквизиты лицензии на медицинскую деятельность",
        "law": "Правила платных медуслуг (реквизиты лицензии) + 118н",
        "text_patterns": [
            r"лицензи\w*", r"№\s*ло[-\s]?\d", r"\bло-\d{2}", r"\bл041-",
            r"лицензи\w*\s+на\s+осуществление\s+медицинск",
        ],
        "markup_patterns": [r"licen[sz]"],
        "link_patterns": [r"лицензи", r"licen"],
        "caveat": "",
    },
    "prices": {
        "label": "Перечень платных услуг с ценами в рублях",
        "law": "Правила платных медуслуг п.17а (цены в рублях) + 118н",
        "text_patterns": [
            r"прайс", r"стоимост\w*\s+услуг", r"цены\s+на\s+услуг", r"наши\s+цены",
            r"\d[\d\s ]*(?:₽|руб\.?\b|р\.\b)",
        ],
        "markup_patterns": [r"price", r"прайс", r"tariff"],
        "link_patterns": [r"цен[аыы]?", r"прайс", r"стоимост", r"price", r"услуг\w*\s*и\s*цен"],
        "caveat": "",
    },
    "doctors": {
        "label": "Сведения о врачах (специальность, квалификация)",
        "law": "Правила платных медуслуг п.17г + 118н (сведения о медработниках)",
        "text_patterns": [
            r"наши\s+врачи", r"наши\s+специалист", r"наша\s+команда",
            r"врачи\s+клиник", r"наши\s+доктора", r"врач[-\s]?стоматолог",
        ],
        "markup_patterns": [],
        "link_patterns": [r"врач", r"специалист", r"команда", r"доктор", r"vrach", r"doctor", r"team", r"o[-_]nas"],
        "caveat": "",
    },
}

# Ссылки на страницы, которые имеет смысл дозагрузить (максимум по одной каждого
# рода): страница-политика и страница-цены. Ключи из CHECKS, чьи link_patterns
# используем как «навигационные».
SUBPAGE_LINK_KINDS = {
    "policy": ["политик", "конфиденциальн", "персональн", "privacy", "policy"],
    "prices": ["прайс", "стоимост", "цены", "price", "услуги и цены"],
}


def normalize_url(url):
    """'clinic.ru' / 'http://clinic.ru/' -> 'http://clinic.ru' с схемой."""
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "http://" + u
    return u


def _strip_to_text(html):
    no_ss = _SCRIPT_STYLE_RE.sub(" ", html)
    txt = _TAG_RE.sub(" ", no_ss)
    txt = re.sub(r"&[a-z]+;|&#\d+;", " ", txt)
    return _WS_RE.sub(" ", txt).lower()


def _links(html, base_url):
    out = []
    for m in _A_RE.finditer(html):
        href = m.group(1).strip()
        anchor = _WS_RE.sub(" ", _TAG_RE.sub(" ", m.group(2))).strip().lower()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            # ссылки без цели пропускаем, но их текст ещё может быть полезен —
            # тем не менее без href навигацией они не являются
            if not anchor:
                continue
        out.append((href, anchor))
    return out


class Fetched:
    """Результат одного HTTP-запроса страницы."""
    def __init__(self, ok, status, final_url, html, error=""):
        self.ok = ok
        self.status = status
        self.final_url = final_url
        self.html = html or ""
        self.error = error


def _decode(raw, ctype):
    """Правильно раскодировать байты страницы. КРИТИЧНО для RU-сайтов: без
    charset в заголовке requests угадывает ISO-8859-1, и вся кириллица
    превращается в мусор ('Ð¡Ñ\x82...') — тогда ВСЕ текстовые проверки ложно
    дают «отсутствует». Порядок: charset из заголовка -> <meta charset> в самих
    байтах -> utf-8 -> cp1251 (вторая по частоте кодировка рунета)."""
    enc = None
    m = re.search(r"charset=\s*[\"']?\s*([\w-]+)", ctype or "", re.I)
    if m:
        enc = m.group(1)
    if not enc:
        m = re.search(rb"""charset\s*=\s*["']?\s*([\w-]+)""", raw[:4096], re.I)
        if m:
            try:
                enc = m.group(1).decode("ascii", errors="ignore")
            except Exception:
                enc = None
    for candidate in [enc, "utf-8", "cp1251"]:
        if not candidate:
            continue
        try:
            txt = raw.decode(candidate, errors="strict")
            return txt
        except (LookupError, UnicodeDecodeError):
            continue
    # ничего строго не сошлось — utf-8 с заменой (лучше, чем cp1251-мусор)
    return raw.decode("utf-8", errors="replace")


def _fetch(session, url):
    try:
        r = session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                        allow_redirects=True, stream=True)
        status = r.status_code
        final = r.url
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype.lower() and "text" not in ctype.lower() and ctype:
            r.close()
            return Fetched(False, status, final, "", f"не HTML (Content-Type: {ctype[:60]})")
        # чанковое чтение с жёстким дедлайном: медленный «ручеёк» не растянет
        # загрузку на десятки секунд (см. комментарий к MAX_TOTAL_SEC выше)
        deadline = time.time() + MAX_TOTAL_SEC
        chunks, total = [], 0
        for chunk in r.iter_content(16384):
            if chunk:
                chunks.append(chunk)
                total += len(chunk)
            if total >= MAX_HTML_BYTES or time.time() > deadline:
                break
        r.close()
        raw = b"".join(chunks)
        html = _decode(raw, ctype)
        return Fetched(status == 200, status, final, html,
                       "" if status == 200 else f"HTTP {status}")
    except requests.exceptions.SSLError as e:
        return Fetched(False, 0, url, "", f"SSL: {str(e)[:120]}")
    except requests.exceptions.ConnectionError as e:
        return Fetched(False, 0, url, "", f"соединение: {str(e)[:120]}")
    except requests.exceptions.Timeout:
        return Fetched(False, 0, url, "", "таймаут соединения/чтения")
    except Exception as e:
        return Fetched(False, 0, url, "", f"{type(e).__name__}: {str(e)[:120]}")


def _run_checks(text, html_lc, links):
    """Прогнать все CHECKS по собранным корпусам. Возвращает dict:
    {ключ: {"present": bool, "evidence": str}}."""
    results = {}
    link_blob = " ".join(f"{h} {a}" for h, a in links)
    for key, spec in CHECKS.items():
        present = False
        evidence = ""
        for pat in spec.get("text_patterns", []):
            m = re.search(pat, text)
            if m:
                present, evidence = True, _snippet(text, m)
                break
        if not present:
            for pat in spec.get("markup_patterns", []):
                m = re.search(pat, html_lc)
                if m:
                    present, evidence = True, f"маркер разметки: {m.group(0)[:60]}"
                    break
        if not present:
            for pat in spec.get("link_patterns", []):
                m = re.search(pat, link_blob)
                if m:
                    present, evidence = True, f"ссылка/раздел: {m.group(0)[:60]}"
                    break
        results[key] = {"present": present, "evidence": evidence}
    return results


def _snippet(text, m, radius=40):
    a = max(0, m.start() - radius)
    b = min(len(text), m.end() + radius)
    return ("…" + text[a:b].strip() + "…")[:140]


def _pick_subpages(links, base_url):
    """Выбрать до одной ссылки-политики и одной ссылки-цен на том же домене."""
    base = urlparse(base_url)
    picked = {}
    for kind, needles in SUBPAGE_LINK_KINDS.items():
        for href, anchor in links:
            blob = f"{href.lower()} {anchor}"
            if any(n in blob for n in needles):
                absu = urldefrag(urljoin(base_url, href))[0]
                pu = urlparse(absu)
                if pu.scheme in ("http", "https") and pu.netloc == base.netloc:
                    picked[kind] = absu
                    break
    return picked


def audit_site(url, session=None, robots_cache=None, log=print, pause=(0.8, 1.6)):
    """Проверить один сайт. Возвращает dict с результатами (готов к записи в БД).

    Максимум 3 запроса: главная + до 2 доп. страниц (политика, цены), и только
    если ссылки на них есть и robots.txt не против."""
    own_session = session is None
    if own_session:
        session = requests.Session()
        session.headers.update({"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"})
    if robots_cache is None:
        robots_cache = {}

    norm = normalize_url(url)
    result = {
        "url": url, "normalized_url": norm, "fetched_ok": 0, "http_status": 0,
        "final_url": "", "pages_fetched": 0, "checks": {}, "missing": [],
        "present": [], "score": 0, "max_score": len(CHECKS), "error": "",
    }
    if not norm:
        result["error"] = "пустой URL"
        return result

    home = _fetch(session, norm)
    result["http_status"] = home.status
    result["final_url"] = home.final_url
    if not home.ok:
        result["error"] = home.error or "главная не открылась"
        if own_session:
            session.close()
        return result

    result["fetched_ok"] = 1
    result["pages_fetched"] = 1
    result["thin_content"] = 0
    html_lc = home.html.lower()
    text = _strip_to_text(home.html)
    links = _links(home.html, home.final_url)

    # доп. страницы (политика/цены) — только по найденным на главной ссылкам.
    # Вежливость здесь обеспечивается не запросом robots.txt (он сам по себе
    # добавлял до +10с латентности на домен при копеечной пользе на нашем
    # объёме), а жёстким инвариантом: на ОДИН домен максимум 3 запроса —
    # главная + политика + цены, ровно те страницы, что открыл бы живой
    # посетитель, — с паузой между ними и коротким таймаутом. Это и есть
    # «разумное отношение к robots»: мы физически не можем задолбить сайт.
    subpages = _pick_subpages(links, home.final_url)
    for kind, suburl in subpages.items():
        time.sleep(random.uniform(*pause))
        sub = _fetch(session, suburl)
        if sub.ok:
            result["pages_fetched"] += 1
            html_lc += " " + sub.html.lower()
            text += " " + _strip_to_text(sub.html)
            links += _links(sub.html, sub.final_url)

    # Тонкий/JS-контент: если видимого текста почти нет, сайт, скорее всего,
    # рендерится JavaScript'ом (SPA/конструктор с client-side отрисовкой), и
    # статический аудит по нему НЕНАДЁЖЕН — любые «отсутствует» тут особенно
    # подозрительны. Помечаем весь результат как требующий ручной проверки,
    # а не выдаём длинный список «пробелов» как факт.
    THIN = 400
    if len(text.strip()) < THIN:
        result["thin_content"] = 1

    checks = _run_checks(text, html_lc, links)
    result["checks"] = checks
    for key, r in checks.items():
        if r["present"]:
            result["present"].append(key)
        else:
            result["missing"].append(key)
    result["score"] = len(result["present"])

    if own_session:
        session.close()
    return result


# ------------------------------------------------------------------ хранение
AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS site_audit (
    dedupe_key   TEXT PRIMARY KEY,
    org_name     TEXT,
    region       TEXT,
    city         TEXT,
    url          TEXT,
    final_url    TEXT,
    fetched_ok   INTEGER NOT NULL DEFAULT 0,
    http_status  INTEGER,
    pages_fetched INTEGER,
    thin_content INTEGER DEFAULT 0,   -- 1 = мало текста (вероятно JS-рендер), аудит ненадёжен
    score        INTEGER,
    max_score    INTEGER,
    missing      TEXT,          -- '; '-список ключей вероятно отсутствующих элементов
    missing_labels TEXT,        -- человекочитаемые названия тех же элементов
    checks_json  TEXT,          -- полный JSON {ключ: {present, evidence}}
    error        TEXT,
    audited_at   TEXT
);
"""


def ensure_audit_table(conn):
    conn.executescript(AUDIT_SCHEMA)
    conn.commit()


def save_audit(conn, lead, result, audited_at):
    """Записать результат аудита одной клиники. Идемпотентно по dedupe_key
    (REPLACE) — повторный прогон обновляет строку, а не плодит дубли."""
    missing_keys = result.get("missing", [])
    missing_labels = [CHECKS[k]["label"] for k in missing_keys]
    conn.execute(
        "INSERT OR REPLACE INTO site_audit (dedupe_key, org_name, region, city, url, "
        "final_url, fetched_ok, http_status, pages_fetched, thin_content, score, max_score, "
        "missing, missing_labels, checks_json, error, audited_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (lead.get("dedupe_key"), lead.get("org_name"), lead.get("region"),
         lead.get("city"), result.get("url"), result.get("final_url"),
         result.get("fetched_ok", 0), result.get("http_status", 0),
         result.get("pages_fetched", 0), result.get("thin_content", 0),
         result.get("score", 0), result.get("max_score", len(CHECKS)),
         "; ".join(missing_keys), "; ".join(missing_labels),
         json.dumps(result.get("checks", {}), ensure_ascii=False),
         result.get("error", ""), audited_at))


def already_audited(conn):
    """dedupe_key уже проверенных клиник (для дозапуска после прерывания).
    Ошибочные (не открылся сайт) НЕ считаем завершёнными — их стоит перепробовать."""
    try:
        cur = conn.execute("SELECT dedupe_key FROM site_audit WHERE fetched_ok = 1")
        return {r[0] for r in cur.fetchall()}
    except Exception:
        return set()
