# -*- coding: utf-8 -*-
"""
Обогащение лидов правовой формой (ИП / ООО / гос / прочее / неизвестно).

Зачем: по гипотезе владельца самые горячие лиды на разработку сайта — ИП
(одиночные частные кабинеты), а не сети/ООО. Карточки карт дают только
бренд («Стоматология Улыбка»), не юрлицо — этот модуль пытается восстановить
правовую форму по открытым реестрам.

Что РЕАЛЬНО проверено с этой машины 27.08.2026 (не предположения):

1. egrul.nalog.ru (официальный бесплатный поиск ФНС по ЕГРЮЛ/ЕГРИП) —
   РАБОТАЕТ программно: POST / с query+region -> {"t": токен,
   "captchaRequired": false}, затем GET /search-result/<t> -> JSON rows.
   Капчи на тестовых запросах не было. Никакого обхода капчи в коде нет
   и не будет: если captchaRequired станет true — прогон честно
   останавливается (см. SourceUnavailable), необработанные лиды остаются
   необработанными до следующего запуска.
   Формат строки ответа: "k" = "ul" (юрлицо) | "fl" (ИП), "n" = полное
   имя (для ИП — ФИО), "c" = краткое имя юрлица (ООО "ТРИОДЕНТ"),
   "i" = ИНН, "o" = ОГРН/ОГРНИП, "e" = дата прекращения (если есть),
   "r" = дата регистрации, "rn" = регион, "tot" = всего найдено.
   Поиск нечёткий (по слову «АБСОЛЮТ-ДЕНТ» вернул 140 строк) — поэтому
   обязательна строгая пост-фильтрация по нормализованному имени.

2. Страница организации Яндекс.Карт (source_url лида) юр. информации НЕ
   содержит — проверено на живой странице: ни ОГРН, ни ИНН, ни «ООО …».
   Перечитывать source_url бессмысленно, эта ветка не строится.

3. DaData suggest/party без ключа -> 401. Бесплатный тариф (~10k
   подсказок/сутки) требует регистрации владельцем на dadata.ru и ключа
   в .env (DADATA_API_KEY). Бэкенд реализован и включается автоматически,
   когда ключ появится; без ключа основной путь — egrul.

Честные ограничения соответствия (главная проблема — matching, не доступ):
- Бренд ИП-кабинета в ЕГРИП НЕ ищется: ИП зарегистрирован на ФИО, вывеска
  «Улыбка» в реестре не фигурирует. Поэтому «ИП не нашёлся по бренду» — это
  НЕ доказательство, что лид не ИП; это unknown.
- Совпадение по имени ООО в том же регионе — не гарантия, что это ТА САМАЯ
  организация (в выдаче нет ОКВЭД и города, только регион). Уверенность
  отражается полем confidence, для распространённых имён («Улыбка»,
  «Дантист») она принудительно низкая.
- Ликвидированное точное совпадение (реальный случай: ООО «АБСОЛЮТ - ДЕНТ»
  ликвидировано 06.10.2025, клиника работает) — это unknown с пометкой,
  не «ооо»: клиника могла перерегистрироваться, в т.ч. в ИП.
- Значимая доля лидов ЗАКОНОМЕРНО останется unknown — честный unknown
  лучше выдуманной уверенности.

Конкурентность: модуль только ЧИТАЕТ таблицу leads и пишет в ОТДЕЛЬНУЮ
таблицу legal_form_enrichment (CREATE TABLE IF NOT EXISTS, без ALTER).
Существующий пайплайн не трогается. Результат каждого лида коммитится
СРАЗУ (требование владельца после реального почти-провала: упавший процесс
теряет максимум один лид в полёте, не весь прогон). Повторный запуск
пропускает уже обогащённые лиды.

Запуск:
    python -m src.enrich_legal_form                # основная база
    python -m src.enrich_legal_form --limit 20     # калибровочный прогон
    python -m src.enrich_legal_form --offline-only # только эвристики, без сети
    python -m src.enrich_legal_form --db-path data/parallel/ural.sqlite3
"""
import argparse
import io
import json
import random
import re
import sys
import time
from pathlib import Path

import requests

try:
    from .db import DB_PATH, now_iso
except ImportError:  # запуск как скрипт из корня проекта
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.db import DB_PATH, now_iso

# ---------------------------------------------------------------------------
# Схема: отдельная таблица, ключ = dedupe_key лида. НИКАКИХ ALTER leads.
# ---------------------------------------------------------------------------
ENRICH_SCHEMA = """
CREATE TABLE IF NOT EXISTS legal_form_enrichment (
    dedupe_key   TEXT PRIMARY KEY,
    org_name     TEXT,
    region       TEXT,
    city         TEXT,
    legal_form   TEXT NOT NULL,   -- ip | ooo | gov | other | unknown
    confidence   TEXT NOT NULL,   -- high | medium | low | none
    method       TEXT NOT NULL,   -- как получен результат, см. код
    matched_name TEXT,            -- найденное в реестре имя (если есть)
    matched_inn  TEXT,
    matched_ogrn TEXT,
    match_details TEXT,           -- JSON: кандидаты, ликвидированные и т.п.
    source       TEXT,            -- egrul | dadata | heuristic
    error        TEXT,
    enriched_at  TEXT
);
"""

# Код региона ФНС по имени региона, как оно записано в leads.region.
# Покрывает все регионы из config/regions.py (включая ещё собираемые).
REGION_CODES = {
    "Тюменская область": "72",
    "Краснодарский край": "23",
    "Москва": "77",
    "Московская область": "50",
    "Свердловская область": "66",
    "Ростовская область": "61",
    "Красноярский край": "24",
    "Санкт-Петербург": "78",
    "Ленинградская область": "47",
    "Татарстан": "16",
    "Башкортостан": "02",
    "Самарская область": "63",
    "Челябинская область": "74",
    "Новосибирская область": "54",
    "Нижегородская область": "52",
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# Пэйсинг консервативный сознательно: ФНС — государственный сервис, и включённая
# там капча останавливает обогащение целиком (обход капчи запрещён правилом
# задачи). Дешевле идти медленно, чем упереться в проверку на середине прогона.
#
# Здесь раньше стояло другое обоснование — "IP этой машины уже добился блокировки
# у 2ГИС объёмом скрейпинга". Это оказалось неверно: блок 2ГИС был не по IP, а по
# TLS-отпечатку клиента (проверено 29.08.2026, см. docstring src/scrapers/gis2.py).
# Само значение пауз при этом НЕ трогаем — оно консервативное и осталось верным,
# сменилась только причина, по которой оно такое.
PAUSE_BETWEEN_SEARCHES = (2.5, 4.0)   # сек между поисковыми обращениями
PAUSE_POST_TO_GET = (1.2, 1.9)        # сек между POST (токен) и GET (результат)


class SourceUnavailable(Exception):
    """Источник недоступен (капча/блок/сеть) — прогон честно останавливается."""


# ---------------------------------------------------------------------------
# Нормализация имён
# ---------------------------------------------------------------------------
_QUOTES = "«»\"'`„“”‚’"

# Слова-описатели, не несущие имени бренда (регексы — чтобы покрыть падежи:
# «стоматология / стоматологии / стоматологическая / стоматологический»).
_DESCRIPTOR_RES = [re.compile(p, re.I) for p in (
    r"^стоматологи\w*$", r"^клиник\w*$", r"^кабинет\w*$", r"^центр\w*$",
    r"^поликлиник\w*$", r"^студи\w*$", r"^салон\w*$", r"^семейн\w*$",
    r"^детск\w*$", r"^медицинск\w*$", r"^медицин\w*$", r"^мц$", r"^врач\w*$",
    r"^зубн\w*$", r"^эстетическ\w*$", r"^ортопедическ\w*$",
    r"^ортодонтическ\w*$", r"^профессиональн\w*$", r"^косметологи\w*$",
    r"^доктор\w*$", r"^стоматолог\w*$", r"^дантист\w*$",
)]

# Имена, слишком распространённые, чтобы совпадение по имени в масштабе
# РЕГИОНА (город в выдаче ЕГРЮЛ не виден) считалось надёжным.
COMMON_BRAND_RESIDUALS = {
    "улыбка", "улыбок", "смайл", "smile", "дент", "дента", "дентал",
    "дантист", "эталон", "жемчуг", "жемчужина", "гиппократ", "диамант",
    "маэстро", "династия", "мята", "вероника", "мария", "полина", "аврора",
    "здоровье", "доверие", "престиж", "элит", "люкс",
    # имена, повторяющиеся в самом корпусе 355 лидов в разных городах/регионах —
    # прямое доказательство распространённости (значит, совпадение по имени
    # в масштабе региона слабое): МастерДент есть и в Армавире, и в Тобольске
    "мастердент", "мастер-дент", "неодент", "идеалдент", "денталюкс",
    "новодент", "стомадент", "радент", "медсервис", "полимед",
}

_GOV_RE = re.compile(
    r"поликлиник|больниц|муниципальн|государствен|\bгбуз\b|\bмбуз\b|"
    r"\bмауз\b|\bгауз\b|\bцрб\b|госпитал|минздрав", re.I)

_IP_MARKER_RE = re.compile(r"(?:^|[\s,«\"(])(ип|чп)(?:[\s.»\")]|$)", re.I)

# Полное ФИО: «Беспокоев Антон Владимирович»
_FULL_FIO_RE = re.compile(
    r"([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+(?:вич|вна|ична|инична))\b")
# Фамилия + инициалы: «Папян В. А.», «Захарченко Г.А.»
_SURNAME_INITIALS_RE = re.compile(
    r"([А-ЯЁ][а-яё]{2,})\s+([А-ЯЁ])\s*\.\s*([А-ЯЁ])\s*\.")
# «доктора Тарского», «Доктор Хан», «врача Иванова» — первое слово может быть
# с заглавной, но фамилия обязана быть капитализированной (без re.I целиком:
# иначе [А-ЯЁ] перестанет требовать заглавную и полезут ложные срабатывания)
_DOCTOR_RE = re.compile(r"(?:[Дд]октора|[Дд]октор|[Вв]рача|[Вв]рач)\s+([А-ЯЁ][а-яё]{2,})")
# Родительный падеж имени перед фамилией: «Лидии Чижевской», «Льва Левченко»
_GIVEN_GEN = {
    "льва": "Лев", "лидии": "Лидия", "анны": "Анна", "ольги": "Ольга",
    "елены": "Елена", "ирины": "Ирина", "натальи": "Наталья",
    "сергея": "Сергей", "андрея": "Андрей", "александра": "Александр",
    "татьяны": "Татьяна", "марии": "Мария", "светланы": "Светлана",
}
# Фамильные суффиксы (номинатив и родительный) для остатка из одного слова
_SURNAME_SUFFIX_RE = re.compile(
    r"(ов|ова|ева|ев|ёв|ин|ина|ын|ский|ская|цкий|цкая|ского|ской|цкого|цкой|"
    r"ко|ук|юк|ян|янц|дзе|швили)$", re.I)


def _norm(s):
    """Нижний регистр, ё->е, без кавычек/пунктуации, схлопнутые пробелы."""
    s = str(s or "").lower().replace("ё", "е")
    for q in _QUOTES:
        s = s.replace(q, " ")
    s = re.sub(r"[^\w\s-]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def _tight(s):
    """Для сравнения имён: без пробелов и дефисов («абсолют - дент» == «абсолют-дент»)."""
    return re.sub(r"[\s\-–—_]+", "", _norm(s))


def strip_descriptors(name):
    """«Семейная Стоматология "Здоровье"» -> «здоровье» (отличительная часть)."""
    words = _norm(name).split()
    kept = [w for w in words
            if not any(rx.match(w) for rx in _DESCRIPTOR_RES)
            and w not in ("и", "в", "на", "для", "no", "-")]
    return " ".join(kept)


def surname_nominative_candidates(word):
    """Кандидаты именительного падежа фамилии из (возможно) родительного.

    «Тарского» -> [Тарский]; «Стрельникова» -> [Стрельников, Стрельникова]
    (второе — женская фамилия в именительном, тоже валидная гипотеза);
    уже-именительное слово возвращается как есть.
    """
    w = word.strip().capitalize()
    out = []
    low = w.lower()
    if low.endswith("ского"):
        out.append(w[:-3] + "ий")
    elif low.endswith("цкого"):
        out.append(w[:-3] + "ий")
    elif low.endswith("ого"):
        out.append(w[:-3] + "ый")
    elif low.endswith("ской") or low.endswith("цкой"):
        out.append(w[:-2] + "ая")
    elif low.endswith(("ова", "ева", "ина", "ына")):
        out.append(w[:-1])       # Стрельникова -> Стрельников (род.п. мужской)
        out.append(w)            # Стрельникова — женская фамилия, именительный
    else:
        out.append(w)
    # убрать дубли, сохранив порядок
    seen, uniq = set(), []
    for c in out:
        if c.lower() not in seen:
            seen.add(c.lower())
            uniq.append(c)
    return uniq


def extract_person(name):
    """Кандидаты «за брендом стоит конкретный человек».

    Возвращает dict или None:
      {"surnames": [кандидаты фамилии в именительном],
       "given": имя или None, "patronymic": отчество или None,
       "initials": ("В","А") или None,
       "strength": "full_fio" | "initials" | "doctor" | "residual_surname"}
    """
    raw = str(name or "")
    m = _FULL_FIO_RE.search(raw)
    if m:
        return {"surnames": [m.group(1)], "given": m.group(2),
                "patronymic": m.group(3), "initials": None,
                "strength": "full_fio"}
    m = _SURNAME_INITIALS_RE.search(raw)
    if m:
        return {"surnames": surname_nominative_candidates(m.group(1)),
                "given": None, "patronymic": None,
                "initials": (m.group(2), m.group(3)), "strength": "initials"}
    m = _DOCTOR_RE.search(raw)
    if m:
        return {"surnames": surname_nominative_candidates(m.group(1)),
                "given": None, "patronymic": None, "initials": None,
                "strength": "doctor"}
    # «стоматология Лидии Чижевской» — имя в родительном + фамилия
    words = _norm(raw).split()
    for i, w in enumerate(words[:-1]):
        if w in _GIVEN_GEN:
            return {"surnames": surname_nominative_candidates(words[i + 1]),
                    "given": _GIVEN_GEN[w], "patronymic": None,
                    "initials": None, "strength": "doctor"}
    # остаток из одного слова с фамильным суффиксом: «Стамов», «Петренко»,
    # «Клиника Каинова» -> residual «каинова»
    residual = strip_descriptors(raw)
    parts = residual.split()
    if len(parts) == 1 and len(parts[0]) >= 4 and _SURNAME_SUFFIX_RE.search(parts[0]) \
            and re.fullmatch(r"[а-яё-]+", parts[0]):
        return {"surnames": surname_nominative_candidates(parts[0]),
                "given": None, "patronymic": None, "initials": None,
                "strength": "residual_surname"}
    return None


# ---------------------------------------------------------------------------
# Разбор строк реестра (общий внутренний формат для egrul и dadata)
# ---------------------------------------------------------------------------
_OPF_MAP = {
    "ооо": "ooo",
    "общество с ограниченной ответственностью": "ooo",
    "ао": "other", "зао": "other", "оао": "other", "пао": "other",
    "гбуз": "gov", "мбуз": "gov", "мауз": "gov", "гауз": "gov",
    "муп": "gov", "гуп": "gov", "мбу": "gov", "гбу": "gov",
}


def classify_opf(full_or_short_name):
    """ООО «Х» -> 'ooo'; ГБУЗ/МУП/учреждение -> 'gov'; АО/АНО/проч. -> 'other'."""
    n = _norm(full_or_short_name)
    first = n.split()[0] if n.split() else ""
    if first in _OPF_MAP:
        return _OPF_MAP[first]
    if n.startswith("общество с ограниченной ответственностью"):
        return "ooo"
    if re.search(r"учреждение|казенное|бюджетное|муниципальн|государствен", n):
        return "gov"
    return "other"


def quoted_name(short_name):
    """ООО "ТРИОДЕНТ" -> ТРИОДЕНТ (имя без ОПФ). Если кавычек нет — всё после
    первого слова."""
    s = str(short_name or "")
    m = re.search(r'["«]([^"»]+)["»]', s)
    if m:
        return m.group(1)
    parts = s.split(None, 1)
    return parts[1] if len(parts) == 2 else s


class RegRow:
    """Единая строка реестра: и из egrul, и из dadata."""
    __slots__ = ("kind", "name", "short", "inn", "ogrn", "active", "end_date")

    def __init__(self, kind, name, short="", inn="", ogrn="", active=True, end_date=""):
        self.kind = kind          # "ul" | "fl"
        self.name = name          # полное имя (для fl — ФИО)
        self.short = short        # краткое имя юрлица (может быть пустым)
        self.inn = inn
        self.ogrn = ogrn
        self.active = active
        self.end_date = end_date

    def brand_part(self):
        return quoted_name(self.short) if self.short else quoted_name(self.name)

    def legal_form(self):
        if self.kind == "fl":
            return "ip"
        return classify_opf(self.short or self.name)


def parse_egrul_rows(payload):
    rows = []
    for r in (payload or {}).get("rows", []):
        rows.append(RegRow(
            kind=r.get("k", ""),
            name=r.get("n", ""),
            short=r.get("c", ""),
            inn=r.get("i", ""),
            ogrn=r.get("o", ""),
            active=not r.get("e"),
            end_date=r.get("e", ""),
        ))
    return rows


def parse_dadata_rows(payload):
    rows = []
    for s in (payload or {}).get("suggestions", []):
        d = s.get("data", {})
        opf = (d.get("opf") or {}).get("short", "") or ""
        name = (d.get("name") or {})
        state = (d.get("state") or {})
        kind = "fl" if d.get("type") == "INDIVIDUAL" else "ul"
        full = name.get("full_with_opf") or name.get("full") or s.get("value", "")
        short = name.get("short_with_opf") or ""
        if kind == "fl" and not short:
            short = full
        rows.append(RegRow(
            kind=kind, name=full, short=short,
            inn=d.get("inn", "") or "", ogrn=d.get("ogrn", "") or "",
            active=(state.get("status") == "ACTIVE"),
            end_date=str(state.get("liquidation_date") or "")))
    return rows


# ---------------------------------------------------------------------------
# Бэкенды поиска
# ---------------------------------------------------------------------------
class EgrulBackend:
    """Официальный бесплатный поиск ФНС. Без капчи — и без её обхода:
    captchaRequired=true означает немедленную честную остановку прогона."""

    name = "egrul"

    def __init__(self, log=print):
        self.log = log
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://egrul.nalog.ru/index.html",
        })
        self._consecutive_errors = 0

    def search(self, query, region_code):
        try:
            r = self.s.post("https://egrul.nalog.ru/", data={
                "vyp3CaptchaToken": "", "page": "", "query": query,
                "region": region_code or "", "PreventChromeAutocomplete": "",
            }, timeout=30)
            r.raise_for_status()
            j = r.json()
            if j.get("captchaRequired"):
                raise SourceUnavailable(
                    "egrul.nalog.ru запросил капчу — останавливаемся (капчу не обходим); "
                    "перезапустить позже, уже сохранённые лиды не потеряны")
            token = j.get("t")
            if not token:
                raise ValueError(f"нет токена в ответе: {r.text[:200]}")
            time.sleep(random.uniform(*PAUSE_POST_TO_GET))
            r2 = self.s.get(f"https://egrul.nalog.ru/search-result/{token}", timeout=30)
            r2.raise_for_status()
            self._consecutive_errors = 0
            return parse_egrul_rows(r2.json())
        except SourceUnavailable:
            raise
        except Exception as e:
            self._consecutive_errors += 1
            if self._consecutive_errors >= 3:
                raise SourceUnavailable(
                    f"egrul.nalog.ru: 3 ошибки подряд ({e}) — останавливаемся, "
                    "чтобы не добить источник; перезапустить позже") from e
            raise


class DadataBackend:
    """DaData suggest/party. Требует бесплатный ключ (регистрация владельцем
    на dadata.ru), DADATA_API_KEY в .env. Лимит бесплатного тарифа ~10k
    подсказок/сутки — 355 лидов даже близко не подходят к нему."""

    name = "dadata"
    URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party"

    def __init__(self, api_key, log=print):
        self.log = log
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Token {api_key}",
        })
        self._consecutive_errors = 0

    def search(self, query, region_code):
        body = {"query": query, "count": 20}
        if region_code:
            body["locations"] = [{"kladr_id": f"{region_code}00000000000"}]
        try:
            r = self.s.post(self.URL, json=body, timeout=30)
            if r.status_code in (401, 403):
                raise SourceUnavailable(
                    f"DaData отверг ключ (HTTP {r.status_code}) — проверить DADATA_API_KEY в .env")
            r.raise_for_status()
            self._consecutive_errors = 0
            return parse_dadata_rows(r.json())
        except SourceUnavailable:
            raise
        except Exception as e:
            self._consecutive_errors += 1
            if self._consecutive_errors >= 3:
                raise SourceUnavailable(f"DaData: 3 ошибки подряд ({e})") from e
            raise


def load_dadata_key(project_root=None):
    """DADATA_API_KEY из .env (без зависимости python-dotenv — файл простой)."""
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
    env = root / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("DADATA_API_KEY=") :
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ---------------------------------------------------------------------------
# Решение по одному лиду
# ---------------------------------------------------------------------------
def _result(legal_form, confidence, method, matched=None, details=None, source="heuristic"):
    m = matched or {}
    return {
        "legal_form": legal_form, "confidence": confidence, "method": method,
        "matched_name": m.get("name", ""), "matched_inn": m.get("inn", ""),
        "matched_ogrn": m.get("ogrn", ""),
        "match_details": json.dumps(details or {}, ensure_ascii=False),
        "source": source, "error": "",
    }


def classify_offline(org_name):
    """Эвристики без сети. Возвращает результат или None (нужен реестр)."""
    raw = str(org_name or "")
    if _IP_MARKER_RE.search(raw):
        # «Стоматология ИП Носенко», «ПАНАЦЕЯ ЧП КОКАРЕВА Э.Г.» — сама вывеска
        # называет форму. ЧП — устаревший синоним ИП.
        return _result("ip", "high", "name_marker_ip",
                       details={"marker": "ИП/ЧП в названии карточки"})
    if _GOV_RE.search(raw):
        conf = "high" if re.search(
            r"муниципальн|государствен|областн|\bгбуз\b|\bцрб\b", raw, re.I) else "medium"
        # «Стоматологическая поликлиника №1» в РФ почти всегда гос/муниципальная,
        # но без реестрового подтверждения — только medium.
        return _result("gov", conf, "name_marker_gov")
    return None


def _surname_of(fio):
    parts = _norm(fio).split()
    return parts[0] if parts else ""


def match_person(rows, person):
    """Строки реестра (k=fl) против кандидата-человека."""
    cand = {c.lower().replace("ё", "е") for c in person["surnames"]}
    hits = [r for r in rows
            if r.kind == "fl" and r.active and _surname_of(r.name) in cand]
    if not hits:
        return None
    # полное ФИО / инициалы повышают уверенность
    if person["strength"] == "full_fio":
        want = _norm(f"{person['surnames'][0]} {person['given']} {person['patronymic']}")
        exact = [r for r in hits if _norm(r.name).startswith(want)]
        if exact:
            return exact[0], "high", hits
        return None  # фамилия совпала, но не то ФИО — не тот человек
    if person["strength"] == "initials":
        ini = person["initials"]
        exact = []
        for r in hits:
            parts = _norm(r.name).split()
            if len(parts) >= 3 and parts[1][:1] == ini[0].lower() and parts[2][:1] == ini[1].lower():
                exact.append(r)
        if exact:
            return exact[0], "high", hits
        return None
    # doctor / residual_surname: только фамилия. Уникальное совпадение — medium,
    # несколько тёзок — low (кто-то из них может быть не стоматолог вовсе).
    conf = "medium" if len(hits) == 1 else "low"
    if person["strength"] == "residual_surname" and conf == "medium":
        conf = "low"  # «Стамов»-как-бренд — более слабая догадка, чем «доктора Тарского»
    return hits[0], conf, hits


def match_brand(rows, residual, force_common=False):
    """Строки реестра (k=ul) против отличительной части бренда.

    Возвращает (verdict_dict | None). Совпадение — строгое равенство
    нормализованного имени в кавычках («АБСОЛЮТ - ДЕНТ» == «абсолют-дент»).
    force_common=True принудительно считает имя распространённым (случай
    «клиника названа именем собственного города»: найденный на калибровке
    лид «Анапа» в Анапе — ООО "АНАПА" в регионе 23 может быть чем угодно).
    """
    want = _tight(residual)
    if not want:
        return None

    def _eq(row):
        if row.kind != "ul":
            return False
        bp = row.brand_part()
        # либо имя в кавычках целиком («ТРИОДЕНТ»), либо после снятия
        # описателей («НАРОДНАЯ СТОМАТОЛОГИЯ» -> «народная»)
        return _tight(bp) == want or _tight(strip_descriptors(bp)) == want

    exact = [r for r in rows if _eq(r)]
    active = [r for r in exact if r.active]
    dead = [r for r in exact if not r.active]
    common = force_common or want in {_tight(w) for w in COMMON_BRAND_RESIDUALS}
    details = {
        "exact_active": len(active), "exact_liquidated": len(dead),
        "liquidated": [{"name": r.short or r.name, "end": r.end_date} for r in dead[:5]],
        "common_name": common,
    }
    if not active:
        if dead:
            # реальный случай АБСОЛЮТ-ДЕНТ: единственное совпадение ликвидировано —
            # клиника работает под кем-то другим (возможно, как раз ИП). Честно: unknown.
            return _result("unknown", "none", "brand_match_liquidated_only",
                           details=details, source="registry")
        return None
    forms = {r.legal_form() for r in active}
    if len(forms) > 1:
        return _result("unknown", "none", "brand_match_ambiguous_forms",
                       details=details, source="registry")
    form = forms.pop()
    best = active[0]
    matched = {"name": best.short or best.name, "inn": best.inn, "ogrn": best.ogrn}
    if len(active) == 1:
        conf = "low" if common else "high"
    else:
        conf = "low" if common else "medium"
        if common:
            return _result("unknown", "none", "brand_match_common_multiple",
                           details=details, source="registry")
    details["note"] = ("совпадение по имени и региону; город и ОКВЭД выдача ФНС "
                       "не показывает — это может быть другая организация с тем же именем")
    return _result(form, conf, "brand_search", matched=matched,
                   details=details, source="registry")


def decide(org_name, region_code, search_fn, city=""):
    """Полное решение по одному лиду. search_fn(query, region_code) -> [RegRow]."""
    off = classify_offline(org_name)
    person = extract_person(org_name)
    residual = strip_descriptors(org_name)
    # клиника, названная именем собственного города («Анапа» в Анапе) —
    # совпадение с одноимённым юрлицом региона почти ничего не доказывает
    named_after_city = bool(city) and _tight(residual) == _tight(city)

    # Явный ИП/ЧП-маркер: пробуем ещё и подтвердить реестром (бонус, не условие)
    if off and off["method"] == "name_marker_ip" and person:
        try:
            rows = _search_person(search_fn, person, region_code)
            hit = match_person(rows, person)
            if hit:
                r, _, all_hits = hit
                off = _result("ip", "high", "name_marker_ip+egrip",
                              matched={"name": r.name, "inn": r.inn, "ogrn": r.ogrn},
                              details={"candidates": len(all_hits)}, source="registry")
        except SourceUnavailable:
            raise
        except Exception:
            pass
        return off
    if off:
        return off

    # Человек за брендом -> ЕГРИП
    if person:
        rows = _search_person(search_fn, person, region_code)
        hit = match_person(rows, person)
        if hit:
            r, conf, all_hits = hit
            details = {"candidates": len(all_hits), "strength": person["strength"],
                       "note": ("выдача ФНС не показывает вид деятельности ИП — "
                                "совпадение по ФИО/региону, не по стоматологии")}
            return _result("ip", conf, f"person_search_{person['strength']}",
                           matched={"name": r.name, "inn": r.inn, "ogrn": r.ogrn},
                           details=details, source="registry")

    # Бренд -> ЕГРЮЛ
    if not residual:
        return _result("unknown", "none", "generic_name",
                       details={"reason": "название без отличительной части "
                                          "(«Стоматология», «Клиника» и т.п.)"})
    rows = search_fn(residual, region_code)
    verdict = match_brand(rows, residual, force_common=named_after_city)
    if verdict:
        return verdict
    return _result("unknown", "none", "no_match",
                   details={"query": residual,
                            "note": ("в ЕГРЮЛ по имени не нашлось; для ИП-кабинета это "
                                     "ожидаемо — бренд ИП в реестре не фигурирует")},
                   source="registry")


def _search_person(search_fn, person, region_code):
    """Поиск человека: по каждому кандидату фамилии (макс 2 запроса)."""
    rows = []
    seen = set()
    for surname in person["surnames"][:2]:
        q = surname
        if person.get("given"):
            q = f"{surname} {person['given']}"
        for r in search_fn(q, region_code):
            key = (r.kind, r.ogrn or r.name)
            if key not in seen:
                seen.add(key)
                rows.append(r)
        time.sleep(random.uniform(*PAUSE_BETWEEN_SEARCHES) * 0.5)
    return rows


# ---------------------------------------------------------------------------
# Хранение + прогон
# ---------------------------------------------------------------------------
def open_db(db_path=None):
    import sqlite3
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(ENRICH_SCHEMA)
    conn.commit()
    return conn


def already_done(conn, include_errors=True):
    q = "SELECT dedupe_key FROM legal_form_enrichment"
    if not include_errors:
        q += " WHERE error IS NULL OR error = ''"
    return {row[0] for row in conn.execute(q)}


def save_result(conn, lead, res):
    """Немедленная запись + commit: упавший процесс теряет максимум один лид."""
    conn.execute(
        "INSERT OR REPLACE INTO legal_form_enrichment "
        "(dedupe_key, org_name, region, city, legal_form, confidence, method, "
        " matched_name, matched_inn, matched_ogrn, match_details, source, error, enriched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (lead["dedupe_key"], lead["org_name"], lead["region"], lead["city"],
         res["legal_form"], res["confidence"], res["method"],
         res.get("matched_name", ""), res.get("matched_inn", ""),
         res.get("matched_ogrn", ""), res.get("match_details", "{}"),
         res.get("source", ""), res.get("error", ""), now_iso()))
    conn.commit()


def fetch_leads(conn):
    cur = conn.execute(
        "SELECT dedupe_key, org_name, region, city FROM leads ORDER BY region, city, org_name")
    return [dict(zip(("dedupe_key", "org_name", "region", "city"), r)) for r in cur.fetchall()]


def summarize(conn):
    cur = conn.execute(
        "SELECT legal_form, confidence, COUNT(*) FROM legal_form_enrichment "
        "GROUP BY legal_form, confidence ORDER BY legal_form, confidence")
    return cur.fetchall()


def run(db_path=None, limit=0, offline_only=False, retry_errors=False,
        backend=None, log=print):
    conn = open_db(db_path)
    leads = fetch_leads(conn)
    done = already_done(conn, include_errors=not retry_errors)
    todo = [l for l in leads if l["dedupe_key"] not in done]
    if limit:
        todo = todo[:limit]
    log(f"Лидов в базе: {len(leads)}, уже обогащено: {len(done)}, к обработке: {len(todo)}")

    if backend is None and not offline_only:
        key = load_dadata_key()
        if key:
            backend = DadataBackend(key, log=log)
            log("Бэкенд: DaData (ключ найден в .env)")
        else:
            backend = EgrulBackend(log=log)
            log("Бэкенд: egrul.nalog.ru (бесплатный официальный; ключ DaData в .env не найден)")

    cache = {}

    def cached_search(query, region_code):
        key = (query.lower(), region_code)
        if key not in cache:
            time.sleep(random.uniform(*PAUSE_BETWEEN_SEARCHES))
            cache[key] = backend.search(query, region_code)
        return cache[key]

    stats = {"ok": 0, "errors": 0}
    try:
        for i, lead in enumerate(todo, 1):
            region_code = REGION_CODES.get(lead["region"], "")
            try:
                if offline_only:
                    # Сохраняем ТОЛЬКО решённое эвристикой: писать unknown без
                    # обращения к реестру нельзя — иначе полноценный прогон
                    # после пропустит эти лиды как «уже обогащённые».
                    res = classify_offline(lead["org_name"])
                    if res is None:
                        log(f"[{i}/{len(todo)}] {lead['org_name'][:45]:47} -> "
                            f"(нужен реестр, в offline-режиме не сохраняется)")
                        continue
                else:
                    res = decide(lead["org_name"], region_code, cached_search,
                                 city=lead["city"])
                stats["ok"] += 1
            except SourceUnavailable:
                raise
            except Exception as e:
                res = _result("unknown", "none", "error")
                res["error"] = f"{type(e).__name__}: {e}"
                stats["errors"] += 1
            save_result(conn, lead, res)   # сразу на диск, не в конце
            log(f"[{i}/{len(todo)}] {lead['org_name'][:45]:47} -> "
                f"{res['legal_form']}/{res['confidence']} ({res['method']})")
    except SourceUnavailable as e:
        log(f"ОСТАНОВ: {e}")
        log("Уже обработанные лиды сохранены; повторный запуск продолжит с места останова.")
    except KeyboardInterrupt:
        log("Прервано пользователем; всё обработанное — уже в базе.")

    log("")
    log("Итог по таблице legal_form_enrichment:")
    for form, conf, cnt in summarize(conn):
        log(f"  {form:8} {conf:7} {cnt}")
    conn.close()
    return stats


def main(argv=None):
    if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
        # line_buffering: прогресс виден и при перенаправлении в файл — прогон
        # долгий, наблюдать его иначе невозможно
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description="Обогащение лидов правовой формой (ИП/ООО/...)")
    ap.add_argument("--db-path", default=None,
                    help="путь к SQLite (по умолчанию data/leads.sqlite3)")
    ap.add_argument("--limit", type=int, default=0, help="обработать не больше N лидов")
    ap.add_argument("--offline-only", action="store_true",
                    help="только эвристики по названию, без сетевых запросов")
    ap.add_argument("--retry-errors", action="store_true",
                    help="переобработать лиды, завершившиеся ошибкой")
    args = ap.parse_args(argv)
    run(db_path=args.db_path, limit=args.limit, offline_only=args.offline_only,
        retry_errors=args.retry_errors)


if __name__ == "__main__":
    main()
