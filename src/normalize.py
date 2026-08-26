# -*- coding: utf-8 -*-
"""
Нормализация имени/адреса/телефона/email + фильтр "это правда стоматология".

Ключи для дедупликации и разбор адреса — упрощённая версия той же логики,
что в D:\\Мои разработки\\RUSIMEX\\leadgen\\Hlebozavody_BY_KZ\\scripts\\merge_lib.py
(там она проверена на тысячах реальных записей 2ГИС/Яндекс.Карт по СНГ).
Здесь она урезана до одной страны (RU) и без промышленной/сербской специфики,
которая тому проекту была нужна, а этому — нет.
"""
import re

QUOTES = "«»\"'`„“”‚’"
LEGAL_FORMS = r"(ооо|зао|оао|ао|ип|чуп|нко|пао)"

STREET_TYPE = re.compile(
    r"\b(улица|ул|проспект|просп|пр-т|пр|переулок|пер|шоссе|тракт|мкр|микрорайон|"
    r"бульвар|наб|набережная|проезд|дом|д|корп|корпус|стр|строение|кв)\b\.?", re.I)
REGION_SEG = re.compile(r"\b(область|обл|район|р-н|край|округ|ао)\b\.?", re.I)
SETTLEMENT_PREFIX = re.compile(r"^(г|гор|город|с|село|пгт|п|пос|посёлок|поселок|д|деревня)[.\s]+", re.I)


def name_key(name):
    """Свёрнутое имя организации для сравнения ('ООО «Дента-Люкс»' -> 'дента люкс')."""
    if not name:
        return ""
    s = str(name).lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(rf"[{QUOTES}]", " ", s)
    s = re.sub(rf"\b{LEGAL_FORMS}\b\.?", " ", s)
    s = re.sub(r"[,./\\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def city_key(city_or_addr):
    """Первый содержательный сегмент адреса, похожий на населённый пункт."""
    if not city_or_addr:
        return ""
    raw = str(city_or_addr).lower().strip()
    for seg in [x.strip() for x in raw.split(",")]:
        if not seg:
            continue
        if STREET_TYPE.search(seg) or REGION_SEG.search(seg):
            continue
        seg = re.sub(r"^\d{5,6}\s+", "", seg)
        seg = SETTLEMENT_PREFIX.sub("", seg).strip()
        if re.fullmatch(r"[\d\s./-]+", seg):
            continue
        return seg.replace("ё", "е")
    return ""


def street_key(addr):
    """Улица+дом, без города/области — для более точного ключа дедупликации."""
    raw = str(addr or "").lower()
    ck = city_key(addr)
    parts, houses = [], []
    for seg in [s.strip() for s in raw.split(",")]:
        if not seg or REGION_SEG.search(seg):
            continue
        seg = re.sub(r"^\d{5,6}\s+", "", seg)
        bare = SETTLEMENT_PREFIX.sub("", seg).strip()
        if ck and bare == ck:
            continue
        parts.append(STREET_TYPE.sub(" ", bare))
    a = re.sub(r"[^a-zа-яё0-9]+", " ", " ".join(parts))
    toks = [t for t in a.split() if t]
    words = sorted((t for t in toks if len(t) > 2 and not t[0].isdigit()), key=len, reverse=True)
    houses = [t for t in toks if t and t[0].isdigit()]
    if not words or not houses:
        return ""
    return f"{words[0]} {houses[0]}"


def make_dedupe_key(org_name, city, address):
    nk = name_key(org_name)
    ck = city_key(city or address)
    sk = street_key(address)
    if sk:
        return f"{nk}|{ck}|{sk}"
    return f"{nk}|{ck}"


def normalize_phone(raw):
    """RU-номер -> '+7 XXX XXX-XX-XX' или None, если не похоже на реальный номер."""
    if raw is None:
        return None
    s = str(raw)
    s = re.sub(r"\([^)]*[A-Za-zА-Яа-яЁё][^)]*\)", " ", s)  # "(доб. 101)" и т.п. с буквами
    if "доб" in s.lower() or "ext" in s.lower():
        s = re.split(r"доб|ext", s, flags=re.I)[0]
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10 and digits[0] != "7":
        digits = "7" + digits
    if not digits.startswith("7") or len(digits) != 11:
        return None
    body = digits[1:]
    return f"+7 {body[0:3]} {body[3:6]}-{body[6:8]}-{body[8:10]}"


EMAIL_BLACKLIST_DOMAINS = (
    "yandex.ru", "yandex.by", "yandex.kz", "2gis.ru", "2gis.com", "google.com",
    "gstatic.com", "zoon.ru", "flamp.ru", "prodoctorov.ru", "napopravku.ru",
    "docdoc.ru", "yell.ru", "spravker.ru", "sentry.io", "example.com", "domain.com",
    "wix.com", "tilda.ws",
)
EMAIL_BLACKLIST_LOCAL = ("webmaster", "postmaster", "noreply", "no-reply", "abuse", "support")


def clean_email(addr):
    """Оставить только правдоподобный email самой организации, отсеять служебные/чужие."""
    a = str(addr or "").strip().lower()
    if "@" not in a or " " in a or a.count("@") != 1:
        return None
    local, _, domain = a.partition("@")
    if any(domain == d or domain.endswith("." + d) for d in EMAIL_BLACKLIST_DOMAINS):
        return None
    if any(local.startswith(p) for p in EMAIL_BLACKLIST_LOCAL):
        return None
    if "." not in domain or len(domain) < 4:
        return None
    return a


# --- фильтр "это действительно стоматологическая клиника/кабинет" ---

DENTAL_NAME_RE = re.compile(r"стоматолог|дантист|дентал|dental|ортодонт", re.I)
# зуботехнические лаборатории и продажа расходников — не клиника, работают не с пациентами напрямую
NOISE_CATEGORY_RE = re.compile(
    r"зуботехническ|лаборатор|материал|оборудован|склад|курсы|обучен|учебн|аптек|снабжен", re.I)


def is_dental_clinic(name, categories):
    """categories: список строк (названия рубрик/категорий с площадки-источника)."""
    cats = [str(c) for c in (categories or []) if c]
    name_hit = bool(DENTAL_NAME_RE.search(str(name or "")))
    cat_hit = any(DENTAL_NAME_RE.search(c) for c in cats)
    if not (name_hit or cat_hit):
        return False
    if cats and all(NOISE_CATEGORY_RE.search(c) for c in cats):
        return False
    return True
