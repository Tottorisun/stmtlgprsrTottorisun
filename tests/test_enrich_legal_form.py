# -*- coding: utf-8 -*-
"""
Офлайн-тесты обогащения правовой формой (src/enrich_legal_form.py).
Ни одного сетевого запроса: реестр подменяется фикстурами, повторяющими
РЕАЛЬНУЮ форму ответов egrul.nalog.ru и DaData (сняты живьём 27.08.2026,
см. docstring модуля). Все имена в фикстурах — из реального корпуса 355 лидов.
"""
import sqlite3
import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.enrich_legal_form import (  # noqa: E402
    COMMON_BRAND_RESIDUALS, RegRow, classify_offline, classify_opf, decide,
    extract_person, match_brand, match_person, open_db, parse_dadata_rows,
    parse_egrul_rows, quoted_name, save_result, strip_descriptors,
    surname_nominative_candidates, already_done, _tight,
)


# ---------------------------------------------------------------- нормализация
class TestStripDescriptors:
    def test_family_zdorovye(self):
        assert strip_descriptors('Семейная Стоматология "Здоровье"') == "здоровье"

    def test_triodent_untouched(self):
        assert strip_descriptors("ТриоДент") == "триодент"

    def test_generic_becomes_empty(self):
        assert strip_descriptors("Стоматология") == ""
        assert strip_descriptors("Стоматологическая клиника") == ""
        assert strip_descriptors("Центр эстетической стоматологии") == ""

    def test_narodnaya(self):
        assert strip_descriptors("Народная стоматология") == "народная"

    def test_yo_normalized(self):
        assert strip_descriptors("Аёвит") == strip_descriptors("Аевит")

    def test_planeta_detstva_not_stripped(self):
        # «Детства» — не то же слово, что описатель «детская»
        assert "детства" in strip_descriptors("Планета Детства")


class TestTight:
    def test_hyphen_space_equivalence(self):
        # реальный случай: ЕГРЮЛ пишет «АБСОЛЮТ - ДЕНТ», карточка — «Абсолют-Дент»
        assert _tight("АБСОЛЮТ - ДЕНТ") == _tight("Абсолют-Дент")


# ------------------------------------------------------------------- эвристики
class TestOfflineHeuristics:
    def test_ip_marker(self):
        r = classify_offline("Стоматология ИП Носенко")
        assert r["legal_form"] == "ip" and r["confidence"] == "high"

    def test_chp_marker(self):
        r = classify_offline("ПАНАЦЕЯ ЧП КОКАРЕВА Э.Г.")
        assert r["legal_form"] == "ip"

    def test_ip_not_matched_inside_word(self):
        # «Триподент» содержит «ип» внутри слова — не маркер
        assert classify_offline("Триподент") is None
        assert classify_offline("Гиппократ") is None

    def test_gov_polyclinic(self):
        r = classify_offline("Стоматологическая поликлиника № 1")
        assert r["legal_form"] == "gov"

    def test_gov_municipal_high(self):
        r = classify_offline("Детская Стоматологическая Поликлиника Муниципальная")
        assert r["legal_form"] == "gov" and r["confidence"] == "high"

    def test_gov_oblast_hospital(self):
        r = classify_offline("Областная больница № 23, стоматологическое поликлиническое отделение")
        assert r["legal_form"] == "gov" and r["confidence"] == "high"

    def test_plain_brand_needs_registry(self):
        assert classify_offline("ТриоДент") is None


# ------------------------------------------------------- извлечение человека
class TestExtractPerson:
    def test_full_fio(self):
        p = extract_person("Беспокоев Антон Владимирович")
        assert p["strength"] == "full_fio"
        assert p["surnames"] == ["Беспокоев"]
        assert p["given"] == "Антон" and p["patronymic"] == "Владимирович"

    def test_fio_inside_quotes(self):
        p = extract_person('Стоматолог и Я "Кабанец Сергей Александрович"')
        assert p["strength"] == "full_fio" and p["surnames"] == ["Кабанец"]

    def test_surname_initials(self):
        p = extract_person("Стоматолог Папян В. А.")
        assert p["strength"] == "initials"
        assert p["surnames"][0] == "Папян" and p["initials"] == ("В", "А")

    def test_surname_initials_no_spaces(self):
        p = extract_person("Стоматология Захарченко Г.А.")
        assert p["strength"] == "initials" and p["surnames"][0] == "Захарченко"

    def test_doctor_genitive(self):
        p = extract_person("Стоматология доктора Тарского")
        assert p["strength"] == "doctor"
        assert "Тарский" in p["surnames"]

    def test_doctor_nominative(self):
        p = extract_person("Доктор Амирханян")
        assert "Амирханян" in p["surnames"]

    def test_doctor_ova_both_hypotheses(self):
        p = extract_person("Стоматология доктора Стрельникова")
        # мужская (Стрельников) и женская (Стрельникова) гипотезы обе валидны
        assert "Стрельников" in p["surnames"] and "Стрельникова" in p["surnames"]

    def test_given_genitive_pair(self):
        p = extract_person("стоматология Лидии Чижевской")
        assert "Чижевская" in p["surnames"]
        assert p["given"] == "Лидия"

    def test_lev_levchenko(self):
        p = extract_person("Клиника стоматологической медицины Льва Левченко")
        assert "Левченко" in p["surnames"] and p["given"] == "Лев"

    def test_residual_surname(self):
        p = extract_person("Стамов")
        assert p is not None and p["strength"] == "residual_surname"

    def test_klinika_kainova(self):
        p = extract_person("Клиника Каинова")
        assert p is not None
        assert "Каинов" in p["surnames"]

    def test_brand_is_not_person(self):
        assert extract_person("ТриоДент") is None
        assert extract_person("Зубная мудрость") is None
        assert extract_person("Family Smile") is None


class TestSurnameCandidates:
    def test_skogo(self):
        assert surname_nominative_candidates("Тарского") == ["Тарский"]

    def test_skoi(self):
        assert surname_nominative_candidates("Чижевской") == ["Чижевская"]

    def test_ova(self):
        c = surname_nominative_candidates("Духовникова")
        assert c[0] == "Духовников" and "Духовникова" in c

    def test_indeclinable(self):
        assert surname_nominative_candidates("Левченко") == ["Левченко"]
        assert surname_nominative_candidates("Хан") == ["Хан"]


# ------------------------------------------------------------- разбор реестров
# Реальная форма ответа egrul.nalog.ru (снята 27.08.2026, запрос «ТРИОДЕНТ», регион 72)
EGRUL_TRIODENT = {"rows": [{
    "c": 'ООО "ТРИОДЕНТ"', "g": "ДИРЕКТОР: Трофимова Светлана Валерьевна",
    "cnt": "1", "i": "7219010323", "k": "ul",
    "n": 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ТРИОДЕНТ"',
    "o": "1087232016915", "p": "720301001", "r": "15.04.2008",
    "t": "xx", "pg": "1", "tot": "1", "rn": "Тюменская область"}]}

# Реальный ответ на «Тарский» (регион 72): активный ИП + ликвидированное юрлицо
EGRUL_TARSKY = {"rows": [
    {"r": "01.03.2022", "t": "xx", "pg": "1", "tot": "2", "cnt": "2",
     "i": "665897831947", "k": "fl", "n": "ТАРСКИЙ АНТОН АЛЕКСАНДРОВИЧ",
     "o": "322723200016352"},
    {"p": "720301001", "r": "07.04.2003", "c": 'ТГОО "ФЕДЕРАЦИЯ ТАЙСКИЙ БОКС"',
     "t": "xx", "e": "04.02.2008", "i": "7203107351", "k": "ul",
     "rn": "Тюменская область",
     "n": 'ТЮМЕНСКАЯ ГОРОДСКАЯ ОБЩЕСТВЕННАЯ ОРГАНИЗАЦИЯ "ФЕДЕРАЦИЯ ТАЙСКИЙ БОКС"',
     "o": "1037200005534"}]}

# Реальный случай: единственное точное совпадение по бренду ликвидировано
EGRUL_ABSOLUT = {"rows": [
    {"c": 'ООО "АБСОЛЮТ - ДЕНТ"', "e": "06.10.2025", "i": "7203184500",
     "k": "ul", "n": 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "АБСОЛЮТ - ДЕНТ"',
     "o": "1067203362016", "r": "08.11.2006", "t": "xx", "rn": "Тюменская область"},
    {"c": 'ООО "АБСОЛЮТ"', "i": "7203548613", "k": "ul",
     "n": 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "АБСОЛЮТ"',
     "o": "1227200021212", "r": "14.12.2022", "t": "xx", "rn": "Тюменская область"}]}


class TestParseEgrul:
    def test_ul_row(self):
        rows = parse_egrul_rows(EGRUL_TRIODENT)
        assert len(rows) == 1
        r = rows[0]
        assert r.kind == "ul" and r.active and r.inn == "7219010323"
        assert r.legal_form() == "ooo"
        assert r.brand_part() == "ТРИОДЕНТ"

    def test_fl_row_and_liquidated(self):
        rows = parse_egrul_rows(EGRUL_TARSKY)
        ip = rows[0]
        assert ip.kind == "fl" and ip.active and ip.legal_form() == "ip"
        dead = rows[1]
        assert not dead.active and dead.end_date == "04.02.2008"

    def test_empty(self):
        assert parse_egrul_rows({"rows": []}) == []
        assert parse_egrul_rows({}) == []


class TestClassifyOpf:
    def test_ooo(self):
        assert classify_opf('ООО "ТРИОДЕНТ"') == "ooo"
        assert classify_opf('ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "Х"') == "ooo"

    def test_gov(self):
        assert classify_opf('ГБУЗ "Стоматологическая поликлиника №1"') == "gov"
        assert classify_opf("МУНИЦИПАЛЬНОЕ БЮДЖЕТНОЕ УЧРЕЖДЕНИЕ ЗДРАВООХРАНЕНИЯ Х") == "gov"

    def test_other(self):
        assert classify_opf('ЗАО "ДОКТОР-ДЕНТ"') == "other"
        assert classify_opf('ТГОО "ФЕДЕРАЦИЯ ТАЙСКИЙ БОКС"') == "other"

    def test_quoted_name(self):
        assert quoted_name('ООО "ТРИОДЕНТ"') == "ТРИОДЕНТ"
        assert quoted_name("ИП Иванов") == "Иванов"


# --------------------------------------------------------------- матч по бренду
class TestMatchBrand:
    def test_unique_active_distinctive(self):
        rows = parse_egrul_rows(EGRUL_TRIODENT)
        v = match_brand(rows, "триодент")
        assert v["legal_form"] == "ooo" and v["confidence"] == "high"
        assert v["matched_inn"] == "7219010323"

    def test_liquidated_only_is_unknown(self):
        rows = parse_egrul_rows(EGRUL_ABSOLUT)
        v = match_brand(rows, "абсолют-дент")
        assert v["legal_form"] == "unknown"
        assert v["method"] == "brand_match_liquidated_only"
        det = json.loads(v["match_details"])
        assert det["exact_liquidated"] == 1

    def test_hyphen_variants_match(self):
        # «Абсолют-Дент» на карточке против «АБСОЛЮТ - ДЕНТ» в реестре
        rows = [RegRow("ul", 'ООО "АБСОЛЮТ - ДЕНТ"', short='ООО "АБСОЛЮТ - ДЕНТ"',
                       inn="1", ogrn="2", active=True)]
        v = match_brand(rows, strip_descriptors("Абсолют-Дент"))
        assert v["legal_form"] == "ooo"

    def test_common_name_unique_low(self):
        rows = [RegRow("ul", 'ООО "УЛЫБКА"', short='ООО "УЛЫБКА"',
                       inn="1", ogrn="2", active=True)]
        v = match_brand(rows, "улыбка")
        assert v["legal_form"] == "ooo" and v["confidence"] == "low"

    def test_common_name_multiple_unknown(self):
        rows = [RegRow("ul", 'ООО "УЛЫБКА"', short='ООО "УЛЫБКА"', active=True),
                RegRow("ul", 'ООО "УЛЫБКА"', short='ООО "УЛЫБКА"', active=True)]
        v = match_brand(rows, "улыбка")
        assert v["legal_form"] == "unknown"

    def test_mixed_forms_unknown(self):
        rows = [RegRow("ul", 'ООО "РАДЕНТ"', short='ООО "РАДЕНТ"', active=True),
                RegRow("ul", 'АО "РАДЕНТ"', short='АО "РАДЕНТ"', active=True)]
        v = match_brand(rows, "радент")
        assert v["legal_form"] == "unknown"
        assert v["method"] == "brand_match_ambiguous_forms"

    def test_no_match_returns_none(self):
        rows = parse_egrul_rows(EGRUL_TRIODENT)
        assert match_brand(rows, "нанодент") is None

    def test_descriptor_in_registry_name(self):
        # карточка «Народная стоматология», реестр — ООО "НАРОДНАЯ СТОМАТОЛОГИЯ"
        rows = [RegRow("ul", 'ООО "НАРОДНАЯ СТОМАТОЛОГИЯ"',
                       short='ООО "НАРОДНАЯ СТОМАТОЛОГИЯ"', active=True)]
        v = match_brand(rows, strip_descriptors("Народная стоматология"))
        assert v is not None and v["legal_form"] == "ooo"


# -------------------------------------------------------------- матч человека
class TestMatchPerson:
    def test_doctor_unique_medium(self):
        rows = parse_egrul_rows(EGRUL_TARSKY)
        p = extract_person("Стоматология доктора Тарского")
        row, conf, hits = match_person(rows, p)
        assert row.kind == "fl" and conf == "medium" and len(hits) == 1

    def test_full_fio_high(self):
        rows = [RegRow("fl", "БЕСПОКОЕВ АНТОН ВЛАДИМИРОВИЧ", inn="1", ogrn="2", active=True)]
        p = extract_person("Беспокоев Антон Владимирович")
        row, conf, _ = match_person(rows, p)
        assert conf == "high"

    def test_full_fio_wrong_person_rejected(self):
        rows = [RegRow("fl", "БЕСПОКОЕВ ПЁТР ИВАНОВИЧ", active=True)]
        p = extract_person("Беспокоев Антон Владимирович")
        assert match_person(rows, p) is None

    def test_initials_match(self):
        rows = [RegRow("fl", "ПАПЯН ВАГАН АРАМОВИЧ", inn="1", ogrn="2", active=True)]
        p = extract_person("Стоматолог Папян В. А.")
        row, conf, _ = match_person(rows, p)
        assert conf == "high"

    def test_initials_mismatch_rejected(self):
        rows = [RegRow("fl", "ПАПЯН СЕРГЕЙ ГЕОРГИЕВИЧ", active=True)]
        p = extract_person("Стоматолог Папян В. А.")
        assert match_person(rows, p) is None

    def test_inactive_ip_not_matched(self):
        rows = [RegRow("fl", "ТАРСКИЙ АНТОН АЛЕКСАНДРОВИЧ", active=False, end_date="01.01.2024")]
        p = extract_person("Стоматология доктора Тарского")
        assert match_person(rows, p) is None


# ------------------------------------------------------------ decide() целиком
class TestDecide:
    def test_generic_no_search(self):
        calls = []

        def no_search(q, r):
            calls.append(q)
            return []

        v = decide("Стоматология", "72", no_search)
        assert v["legal_form"] == "unknown" and v["method"] == "generic_name"
        assert calls == []  # генерик не тратит сетевые запросы

    def test_brand_found(self):
        v = decide("ТриоДент", "72", lambda q, r: parse_egrul_rows(EGRUL_TRIODENT))
        assert v["legal_form"] == "ooo" and v["source"] == "registry"

    def test_no_match_is_unknown_not_guess(self):
        v = decide("Энже", "72", lambda q, r: [])
        assert v["legal_form"] == "unknown" and v["method"] == "no_match"

    def test_doctor_to_ip(self):
        v = decide("Стоматология доктора Тарского", "72",
                   lambda q, r: parse_egrul_rows(EGRUL_TARSKY))
        assert v["legal_form"] == "ip"
        assert v["method"] == "person_search_doctor"
        assert v["matched_ogrn"] == "322723200016352"

    def test_ip_marker_without_person(self):
        v = decide("Стоматология ИП Носенко", "72", lambda q, r: [])
        assert v["legal_form"] == "ip" and v["confidence"] == "high"

    def test_doctor_capitalized_regression(self):
        # найдено на живой калибровке 28.08.2026: «Доктор Амирханян» (с заглавной)
        # уходил в слабый путь residual_surname из-за регистрозависимого регекса
        p = extract_person("Доктор Амирханян")
        assert p["strength"] == "doctor"

    def test_city_named_clinic_capped_regression(self):
        # найдено на живой калибровке: лид «Анапа» (г. Анапа) совпал с ООО "АНАПА"
        # региона 23 с high — а это может быть что угодно, не клиника
        rows = [RegRow("ul", 'ООО "АНАПА"', short='ООО "АНАПА"',
                       inn="1", ogrn="2", active=True)]
        v = decide("Анапа", "23", lambda q, r: rows, city="Анапа")
        assert v["confidence"] == "low"  # не high

    def test_same_name_other_city_not_capped(self):
        rows = [RegRow("ul", 'ООО "АНАПА"', short='ООО "АНАПА"',
                       inn="1", ogrn="2", active=True)]
        v = decide("Анапа", "23", lambda q, r: rows, city="Сочи")
        assert v["confidence"] == "high"


# ------------------------------------------------------------------ DaData mock
DADATA_MOCK = {"suggestions": [
    {"value": 'ООО "ТРИОДЕНТ"',
     "data": {"inn": "7219010323", "ogrn": "1087232016915", "type": "LEGAL",
              "opf": {"short": "ООО"},
              "name": {"full_with_opf": 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ТРИОДЕНТ"',
                       "short_with_opf": 'ООО "ТРИОДЕНТ"'},
              "state": {"status": "ACTIVE", "liquidation_date": None}}},
    {"value": "ИП Тарский Антон Александрович",
     "data": {"inn": "665897831947", "ogrn": "322723200016352", "type": "INDIVIDUAL",
              "opf": {"short": "ИП"},
              "name": {"full": "Тарский Антон Александрович"},
              "state": {"status": "LIQUIDATED", "liquidation_date": "2024-01-01"}}},
]}


class TestParseDadata:
    def test_rows(self):
        rows = parse_dadata_rows(DADATA_MOCK)
        assert rows[0].kind == "ul" and rows[0].active and rows[0].legal_form() == "ooo"
        assert rows[0].brand_part() == "ТРИОДЕНТ"
        assert rows[1].kind == "fl" and not rows[1].active

    def test_same_matching_path_as_egrul(self):
        v = decide("ТриоДент", "72", lambda q, r: parse_dadata_rows(DADATA_MOCK))
        assert v["legal_form"] == "ooo" and v["matched_inn"] == "7219010323"


# -------------------------------------------------- хранение: сразу и идемпотентно
class TestStorage:
    def _lead(self, key="k1"):
        return {"dedupe_key": key, "org_name": "ТриоДент",
                "region": "Тюменская область", "city": "Тюмень"}

    def test_immediate_commit_survives_new_connection(self, tmp_path):
        db = tmp_path / "t.sqlite3"
        conn = open_db(db)
        v = decide("ТриоДент", "72", lambda q, r: parse_egrul_rows(EGRUL_TRIODENT))
        save_result(conn, self._lead(), v)
        # НЕ закрываем первое соединение (симуляция упавшего процесса) —
        # открываем новое, как сделал бы перезапуск
        conn2 = sqlite3.connect(db)
        row = conn2.execute(
            "SELECT legal_form, confidence FROM legal_form_enrichment WHERE dedupe_key='k1'"
        ).fetchone()
        assert row == ("ooo", "high")
        conn2.close()
        conn.close()

    def test_resume_skips_done(self, tmp_path):
        db = tmp_path / "t.sqlite3"
        conn = open_db(db)
        v = decide("Стоматология", "72", lambda q, r: [])
        save_result(conn, self._lead("k1"), v)
        done = already_done(conn)
        assert "k1" in done and "k2" not in done
        conn.close()

    def test_errors_excluded_when_retrying(self, tmp_path):
        db = tmp_path / "t.sqlite3"
        conn = open_db(db)
        bad = {"legal_form": "unknown", "confidence": "none", "method": "error",
               "matched_name": "", "matched_inn": "", "matched_ogrn": "",
               "match_details": "{}", "source": "", "error": "boom"}
        save_result(conn, self._lead("k_err"), bad)
        assert "k_err" in already_done(conn, include_errors=True)
        assert "k_err" not in already_done(conn, include_errors=False)
        conn.close()

    def test_no_alter_of_leads_table(self, tmp_path):
        """Гарантия аддитивности: open_db не меняет схему существующей leads."""
        db = tmp_path / "t.sqlite3"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE leads (id INTEGER PRIMARY KEY, dedupe_key TEXT)")
        c.commit()
        before = c.execute("SELECT sql FROM sqlite_master WHERE name='leads'").fetchone()
        c.close()
        conn = open_db(db)
        after = conn.execute("SELECT sql FROM sqlite_master WHERE name='leads'").fetchone()
        assert before == after
        conn.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
