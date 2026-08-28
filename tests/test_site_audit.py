# -*- coding: utf-8 -*-
"""
Офлайн-тесты аудитора сайтов (src/site_audit.py). Ни одного сетевого запроса:
проверяется чистая логика — декодирование кодировок, разбор ссылок, эвристики
проверок и честная пометка «тонкий контент». Сам HTTP (_fetch/audit_site) здесь
не дёргаем — он завязан на сеть и проверялся вживую на dent-rossosh.ru.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.site_audit import (  # noqa: E402
    _decode, _strip_to_text, _links, _run_checks, _pick_subpages,
    normalize_url, CHECKS,
)


class TestDecode:
    def test_utf8_meta_over_wrong_header(self):
        # страница UTF-8, но заголовок молчит про charset -> без meta requests
        # угадал бы latin-1 и всё сломал. Проверяем, что meta charset побеждает.
        html = '<meta charset="utf-8"><p>Стоматология Улыбка</p>'
        raw = html.encode("utf-8")
        out = _decode(raw, "text/html")  # без charset в Content-Type
        assert "Стоматология Улыбка" in out

    def test_cp1251_page(self):
        html = '<meta charset="windows-1251"><p>Политика ПДн</p>'
        raw = html.encode("cp1251")
        out = _decode(raw, "text/html")
        assert "Политика ПДн" in out

    def test_header_charset_used(self):
        raw = "<p>Цены на услуги</p>".encode("utf-8")
        out = _decode(raw, "text/html; charset=utf-8")
        assert "Цены на услуги" in out


class TestStripAndLinks:
    def test_script_style_removed_from_text(self):
        html = "<style>.x{color:red}</style><script>var cookie=1</script><p>Врачи клиники</p>"
        txt = _strip_to_text(html)
        assert "врачи клиники" in txt
        assert "color" not in txt and "var cookie" not in txt

    def test_links_extracted(self):
        html = '<a href="/policy">Политика конфиденциальности</a><a href="#">пусто</a>'
        links = _links(html, "https://x.ru/")
        hrefs = [h for h, a in links]
        assert "/policy" in hrefs

    def test_pick_subpages_same_domain_only(self):
        html = ('<a href="/policy">политика</a>'
                '<a href="https://other.ru/price">цены</a>'
                '<a href="/tseny">прайс услуг</a>')
        links = _links(html, "https://clinic.ru/")
        picked = _pick_subpages(links, "https://clinic.ru/")
        assert picked.get("policy", "").endswith("/policy")
        # ссылка на цены на ЧУЖОМ домене не берётся, берётся внутренняя /tseny
        assert "clinic.ru" in picked.get("prices", "")


class TestRunChecks:
    def test_all_present_compliant_text(self):
        text = (" политика обработки персональных данных согласие на обработку "
                " мы используем файлы cookie версия для слабовидящих лицензия ло-72 "
                " цены на услуги 1500 руб наши врачи стоматолог ")
        res = _run_checks(text, text, [])
        for key in CHECKS:
            assert res[key]["present"], f"{key} должен быть present"

    def test_missing_when_absent(self):
        text = " добро пожаловать в нашу клинику красивые улыбки каждый день "
        res = _run_checks(text, text, [])
        # ни политики, ни согласия, ни слабовидящих в тексте нет
        assert not res["privacy_policy"]["present"]
        assert not res["accessibility"]["present"]

    def test_link_signal_counts_as_present(self):
        # текста нет, но есть ссылка-раздел на политику -> present по ссылке
        links = [("/privacy", "политика конфиденциальности")]
        res = _run_checks(" ", " ", links)
        assert res["privacy_policy"]["present"]

    def test_price_marker_by_ruble_amount(self):
        text = " консультация 500 ₽ лечение кариеса 3000 руб "
        res = _run_checks(text, text, [])
        assert res["prices"]["present"]


class TestNormalizeUrl:
    def test_adds_scheme(self):
        assert normalize_url("clinic.ru").startswith("http://")

    def test_keeps_https(self):
        assert normalize_url("https://clinic.ru/") == "https://clinic.ru/"

    def test_empty(self):
        assert normalize_url("") == ""


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
