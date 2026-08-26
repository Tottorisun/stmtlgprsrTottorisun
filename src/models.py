# -*- coding: utf-8 -*-
"""
Схема лида. Одно место, откуда её берут db.py, export_xlsx.py и pipeline.py —
чтобы порядок и состав колонок не расходились между хранилищем и экспортом.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional

# Порядок = порядок колонок в SQLite и в .xlsx
FIELDS = [
    "dedupe_key", "org_name", "category", "region", "city", "address",
    "phone", "email", "has_website", "source", "source_url", "scraped_at", "notes",
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
    has_website: bool = False  # в этой базе всегда False по построению: это и есть критерий отбора
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
