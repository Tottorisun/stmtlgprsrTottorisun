# -*- coding: utf-8 -*-
"""Общее для всех скрейперов источников."""


class SourceBlocked(Exception):
    """Источник поставил капчу/жёсткий блок. Не обходим — останавливаемся и
    честно фиксируем в progress, а не подсовываем выдуманные данные."""


# Единый "сырой" формат, который отдаёт каждый scrape_region() ДО фильтрации
# и дедупликации (см. src/pipeline.py):
#
# {
#   "name": str,
#   "categories": [str, ...],       # рубрики/категории с самой площадки
#   "city": str,
#   "address": str,
#   "phones_raw": [str, ...],       # ещё не нормализованные
#   "emails_raw": [str, ...],
#   "has_website": bool,            # True/False только если поле сайта реально было в ответе площадки
#   "source": "yandex_maps" | "2gis" | "google_maps",
#   "source_url": str,
# }
RAW_FIELDS = ("name", "categories", "city", "address", "phones_raw", "emails_raw",
              "has_website", "source", "source_url")
