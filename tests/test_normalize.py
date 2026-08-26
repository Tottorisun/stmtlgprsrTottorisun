# -*- coding: utf-8 -*-
"""Офлайн-тесты чистой логики нормализации/фильтрации — без единого сетевого
запроса, специально, чтобы это можно было гонять в CI (см. .github/workflows/ci.yml)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.normalize import normalize_phone, clean_email, is_dental_clinic, make_dedupe_key


def test_normalize_phone_variants_agree():
    assert normalize_phone("+7 (912) 345-67-89") == "+7 912 345-67-89"
    assert normalize_phone("89123456789") == "+7 912 345-67-89"
    assert normalize_phone("79123456789") == "+7 912 345-67-89"


def test_normalize_phone_rejects_garbage():
    assert normalize_phone("123") is None
    assert normalize_phone(None) is None
    assert normalize_phone("") is None


def test_clean_email_accepts_real_org_address():
    assert clean_email("info@aldentis.ru") == "info@aldentis.ru"


def test_clean_email_rejects_aggregator_and_service_addresses():
    assert clean_email("noreply@2gis.ru") is None
    assert clean_email("webmaster@yandex.ru") is None
    assert clean_email("not an email") is None


def test_is_dental_clinic_accepts_clinic_category():
    assert is_dental_clinic("Стом-Мед", ["Стоматологическая клиника"]) is True


def test_is_dental_clinic_rejects_lab_and_supply_noise():
    assert is_dental_clinic("ДентаЛаб", ["зуботехническая лаборатория"]) is False
    assert is_dental_clinic("Дентал-Снаб", ["стоматологические материалы и оборудование"]) is False


def test_is_dental_clinic_rejects_unrelated_business():
    assert is_dental_clinic("Аптека Ромашка", ["Аптека"]) is False
    assert is_dental_clinic("Продукты 24", []) is False


def test_dedupe_key_unifies_name_and_address_variants():
    k1 = make_dedupe_key("ООО «Дента-Люкс»", "Тюмень", "Тюмень, ул. Ленина, 15")
    k2 = make_dedupe_key("Дента Люкс", "Тюмень", "Тюмень, улица Ленина, дом 15")
    assert k1 == k2


def test_dedupe_key_differs_for_different_addresses():
    k1 = make_dedupe_key("Дента Люкс", "Тюмень", "Тюмень, ул. Ленина, 15")
    k2 = make_dedupe_key("Дента Люкс", "Тюмень", "Тюмень, ул. Мельникайте, 70")
    assert k1 != k2
