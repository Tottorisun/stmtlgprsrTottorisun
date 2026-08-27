# -*- coding: utf-8 -*-
"""
Проверяет по-настоящему (не рассуждением о коде), что чек-пойнт по городам в
src/pipeline.py::run_region() реально переживает обрыв процесса посреди
региона -- см. docstring pipeline.py, 27.08.2026.

Фейковый источник без сети/браузера: отдаёт по одной валидной "сырой" записи
на город 1 и город 2, а на городе 3 бросает KeyboardInterrupt -- это НЕ
Exception (Exception её не ловит, см. except-блок в run_region), то есть
честная симуляция "процесс прерван", а не "у одного источника глюк на одном
городе" (тот случай проверять не нужно -- он и так продолжает работу, это и
есть предыдущий уровень устойчивости, который уже был). Открываем НОВОЕ
соединение к тому же файлу SQLite после "падения" (не переиспользуем conn
упавшего запуска) -- это и есть настоящая проверка "данные уже на диске", а
не просто "объект conn ещё что-то помнит в памяти".
"""
import sys
import types
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import pipeline, db

# Свой tempfile.TemporaryDirectory() вместо фикстуры pytest tmp_path: на этой
# машине обнаружился отдельный, не связанный с этим проектом сбой прав доступа
# к C:\Users\User\AppData\Local\Temp\pytest-of-User (собственная "нумерованная"
# temp-директория pytest) -- голый tempfile через стандартную библиотеку идёт
# напрямую в системный TEMP и эту прослойку не трогает.


def _raw(city, i):
    return {
        "name": f"Тестовая стоматология {city} {i}",
        "categories": ["Стоматологическая клиника"],
        "city": city,
        "address": f"{city}, ул. Тестовая, {i}",
        "phones_raw": [f"+7 900 000-00-0{i}"],
        "emails_raw": [],
        "has_website": False,
        "source": "fake_source",
        "source_url": f"https://example.invalid/{city}/{i}",
    }


def _make_fake_scraper(crash_at_city, records_by_city):
    calls = []

    def open_session(log=print):
        calls.append("open")
        return {"opened": True}

    def scrape_city(session, city, log=print):
        calls.append(("scrape", city))
        if city == crash_at_city:
            raise KeyboardInterrupt("симулированный обрыв процесса")
        return records_by_city.get(city, [])

    def close_session(session):
        calls.append("close")

    mod = types.SimpleNamespace(open_session=open_session, scrape_city=scrape_city,
                                 close_session=close_session)
    return mod, calls


def test_interruption_after_city_2_of_4_keeps_earlier_cities_in_sqlite(monkeypatch):
    cities = ["Город1", "Город2", "Город3", "Город4"]
    records_by_city = {
        "Город1": [_raw("Город1", 1)],
        "Город2": [_raw("Город2", 1)],
        # Город3/Город4 никогда не должны быть запрошены за данными -- сбой
        # на входе в Город3 останавливает всё до того, как записи вернутся
        "Город3": [_raw("Город3", 1)],
        "Город4": [_raw("Город4", 1)],
    }
    fake_mod, calls = _make_fake_scraper(crash_at_city="Город3", records_by_city=records_by_city)
    monkeypatch.setitem(pipeline.SCRAPERS, "fake_source", fake_mod)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "interrupt_test.sqlite3"
        conn = db.connect(str(db_path))
        region_cfg = {"name": "Тестовый регион", "cities": cities}

        silent = lambda *a, **k: None  # noqa: E731 -- не засорять вывод теста

        with pytest.raises(KeyboardInterrupt):
            pipeline.run_region("test", region_cfg, ["fake_source"], conn, log=silent)

        # "close_session" по-прежнему должен был выполниться (finally в run_region) --
        # утечка открытой сессии/браузера при падении была бы отдельным багом.
        assert "close" in calls

        conn.close()  # эмулируем реальную смерть процесса -- дальше НЕ переиспользуем
        # этот же объект conn, открываем НОВОЕ соединение к тому же файлу,
        # как это сделал бы перезапущенный процесс.
        conn2 = db.connect(str(db_path))
        all_leads = db.fetch_all(conn2)
        cities_in_db = {l["city"] for l in all_leads}

        assert "Город1" in cities_in_db, "город 1 обработан до сбоя -- должен быть в базе"
        assert "Город2" in cities_in_db, "город 2 обработан до сбоя -- должен быть в базе"
        assert "Город3" not in cities_in_db, "сбой произошёл ДО получения данных по городу 3"
        assert "Город4" not in cities_in_db, "город 4 не должен был вообще запрашиваться"
        assert len(all_leads) == 2
        conn2.close()


def test_ordinary_exception_on_one_city_does_not_abort_region(monkeypatch):
    """Отдельно от KeyboardInterrupt: обычный сбой источника (RuntimeError,
    не BaseException) на ОДНОМ городе не должен ронять прогон всего региона --
    это уровень устойчивости, который был и раньше (per-city try/except
    внутри каждого scraper-модуля), просто теперь живёт в pipeline.py, т.к.
    pipeline.py вызывает scrape_city() напрямую, а не scrape_region()."""
    cities = ["Город1", "ГлючныйГород", "Город3"]
    records_by_city = {
        "Город1": [_raw("Город1", 1)],
        "Город3": [_raw("Город3", 1)],
    }

    def scrape_city(session, city, log=print):
        if city == "ГлючныйГород":
            raise RuntimeError("обычный сетевой сбой на одном городе")
        return records_by_city.get(city, [])

    fake_mod = types.SimpleNamespace(
        open_session=lambda log=print: {}, scrape_city=scrape_city,
        close_session=lambda session: None)
    monkeypatch.setitem(pipeline.SCRAPERS, "fake_source", fake_mod)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "ordinary_error_test.sqlite3"
        conn = db.connect(str(db_path))
        region_cfg = {"name": "Тестовый регион 2", "cities": cities}
        silent = lambda *a, **k: None  # noqa: E731

        # НЕ должно бросить исключение наружу -- обычный RuntimeError на одном
        # источнике/городе перехватывается и логируется, регион идёт дальше.
        leads, stats = pipeline.run_region("test2", region_cfg, ["fake_source"], conn, log=silent)

        cities_in_db = {l["city"] for l in db.fetch_all(conn)}
        assert cities_in_db == {"Город1", "Город3"}
        conn.close()
