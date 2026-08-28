# -*- coding: utf-8 -*-
"""
Схема лида. Одно место, откуда её берут db.py, export_xlsx.py и pipeline.py —
чтобы порядок и состав колонок не расходились между хранилищем и экспортом.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional

# Порядок = порядок колонок в .xlsx и в новых базах. В SQLite колонки
# адресуются по ИМЕНИ (db.py везде перечисляет FIELDS явно, без SELECT * при
# чтении), поэтому физический порядок колонок в старых базах может отличаться —
# это ок. Колонка website добавлена 28.08.2026 при развороте на режим has-site;
# для баз, созданных до этого, db.connect() дозаводит её через ALTER TABLE.
FIELDS = [
    "dedupe_key", "org_name", "category", "region", "city", "address",
    "phone", "email", "has_website", "website", "source", "source_url",
    "scraped_at", "notes",
]


@dataclass
class Lead:
    dedupe_key: str
    org_name: str
    category: str
    region: str
    city: str
    address: str
    phone: str = ""          # "; "-разделённый список, чистый RU-формат "+7 XXX XXX-XX-XX"
    email: str = ""          # почти всегда пусто — карточки на картах email не публикуют
    has_website: bool = False  # в режиме no-site всегда False (это и есть критерий отбора);
                               # в режиме has-site всегда True
    website: str = ""        # URL сайта клиники; заполнен только в режиме has-site
    source: str = ""         # "yandex_maps" или "yandex_maps; 2gis" после слияния
    source_url: str = ""     # "; "-разделённый список ссылок на карточки-источники
    scraped_at: str = ""     # ISO-таймстамп последнего сбора
    notes: str = ""

    def as_row(self):
        d = asdict(self)
        d["has_website"] = int(bool(self.has_website))
        return [d[f] for f in FIELDS]

    def as_dict(self):
        return asdict(self)
