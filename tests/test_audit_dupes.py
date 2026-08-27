# -*- coding: utf-8 -*-
"""Офлайн-тесты для src/audit_dupes.py — без сети, без БД. Проверяет то, что
реально нашлось на живых данных 27.08.2026 (см. README): без GENERIC_NAMES-
защиты одна клиника с общим названием "Стоматология" давала 10 ложных
"вложенных" совпадений почти со всеми остальными записями региона."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audit_dupes import find_possible_duplicates


def _lead(name, city, address, phone, source="yandex_maps"):
    # source_url в реальных данных всегда указывает на домен карты-площадки
    # (yandex.ru/2gis.ru/google.com) -- это домен намеренно исключён из сигнала
    # "общий_домен_в_ссылке" в audit_dupes.py, поэтому используем такой же
    # реалистичный домен здесь, а не общую для обоих лидов заглушку (иначе тест
    # сам создаёт ложное совпадение по домену, не связанное с тем, что проверяем).
    return {"org_name": name, "city": city, "address": address, "phone": phone,
            "source": source, "source_url": f"https://yandex.ru/maps/org/{name}/1/"}


def test_generic_name_does_not_flood_nested_name_signal():
    leads = [
        _lead("Стоматология", "Тюмень", "Тюмень, ул. Бакинских Комиссаров, 1", "+7 345 111-11-11"),
        _lead("Стоматология Мега-Дент", "Тюмень", "ул. Артамонова, 13", "+7 345 222-22-22"),
        _lead("Стоматология Дельта-Стом", "Тюмень", "ул. Гольцова, 8", "+7 345 333-33-33"),
    ]
    groups = find_possible_duplicates(leads)
    nested = [g for g in groups if g["signal"] == "вложенное_название"]
    assert nested == []


def test_fuzzy_name_match_same_city_is_flagged():
    leads = [
        _lead("РИА Дент", "Тюмень", "Тюмень, Холодильная улица, 54/7", "+7 345 111-11-11"),
        _lead("Риа-дент", "Тюмень", "Тюмень, улица Клары Цеткин, 61к4", "+7 345 222-22-22"),
    ]
    groups = find_possible_duplicates(leads)
    fuzzy = [g for g in groups if g["signal"] == "похожие_названия"]
    assert len(fuzzy) == 1
    assert len(fuzzy[0]["leads"]) == 2


def test_distinct_unrelated_clinics_not_flagged():
    leads = [
        _lead("Дента Люкс", "Тюмень", "Тюмень, ул. Ленина, 15", "+7 345 111-11-11"),
        _lead("Жемчужина", "Тобольск", "Тобольск, ул. Ремезова, 5", "+7 345 222-22-22"),
    ]
    assert find_possible_duplicates(leads) == []


def test_shared_phone_digit_across_records_is_flagged():
    leads = [
        _lead("Клиника А", "Тюмень", "ул. Мира, 1", "+7 345 999-99-99"),
        _lead("Клиника Б (другое юрлицо на том же номере)", "Тюмень", "ул. Мира, 1", "+7 345 999-99-99"),
    ]
    groups = find_possible_duplicates(leads)
    phone_groups = [g for g in groups if g["signal"] == "общий_телефон"]
    assert len(phone_groups) == 1
