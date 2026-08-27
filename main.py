# -*- coding: utf-8 -*-
"""
CLI: собрать лиды (стоматологии без сайта) по одному или нескольким регионам.

Примеры:
    python main.py --region tyumen
    python main.py --region tyumen --sources yandex_maps
    python main.py --region moscow --region krasnodar --sources yandex_maps,2gis
    python main.py --list-regions
    python main.py --export-only          # пересобрать .xlsx из уже накопленной SQLite, без сети

    # параллельный сбор несколькими процессами/агентами одновременно: у каждого
    # своя изолированная SQLite (и свой .xlsx рядом с ней) через --db-path,
    # чтобы не было двух одновременных writer'ов в один файл. Слияние всех
    # изолированных баз в основную data/leads.sqlite3 — отдельный шаг после.
    python main.py --region krasnodar --sources yandex_maps --db-path data/parallel/krasnodar.sqlite3
    python main.py --region moscow --sources yandex_maps --db-path data/parallel/moscow.sqlite3

По умолчанию источники: yandex_maps,2gis,google_maps (порядок = приоритет из ТЗ).
2gis и google_maps требуют Playwright + системный Chrome и открывают видимое
окно браузера (headless=False — так меньше похоже на бота). yandex_maps не
требует ни того, ни другого.
"""
import argparse
import sys
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.regions import REGIONS, get_region
from src import db
from src.pipeline import run_region
from src.export_xlsx import export_xlsx
from src.audit_dupes import find_possible_duplicates, format_report

ALL_SOURCES = ["yandex_maps", "2gis", "google_maps"]
OUT_XLSX = Path(__file__).resolve().parent / "out" / "стоматологии_без_сайта.xlsx"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", action="append", dest="regions",
                     help="id региона из config/regions.py (можно указать несколько раз)")
    ap.add_argument("--sources", default=",".join(ALL_SOURCES),
                     help=f"через запятую, из {ALL_SOURCES}")
    ap.add_argument("--list-regions", action="store_true")
    ap.add_argument("--export-only", action="store_true",
                     help="не собирать заново — только пересобрать .xlsx из SQLite")
    ap.add_argument("--audit-only", action="store_true",
                     help="не собирать заново — только прогнать проверку на возможные "
                          "дубли (src/audit_dupes.py) по уже накопленной SQLite")
    ap.add_argument("--cities", default=None,
                     help="через запятую: собрать только эти города из указанного "
                          "региона (имена как в config/regions.py). Нужен для "
                          "дозапуска после прерывания: уже сохранённые по "
                          "чекпоинтам города не пересобираются заново.")
    ap.add_argument("--db-path", default=None,
                     help="путь к отдельному файлу SQLite вместо data/leads.sqlite3 "
                          "(по умолчанию). Нужен для параллельных запусков нескольких "
                          "агентов по разным регионам ОДНОВРЕМЕННО — общий файл SQLite "
                          "с несколькими одновременными writer'ами рискует потерянными "
                          "записями/повреждением; изолированная база на регион/агента "
                          "снимает этот риск, слияние в общую базу — отдельный шаг после.")
    args = ap.parse_args()

    if args.list_regions:
        for rid, cfg in REGIONS.items():
            flag = "подтверждён" if cfg["confirmed"] else "ПРЕДЛОЖЕН, требует подтверждения"
            print(f"{rid:12s} {cfg['name']:28s} [{flag}]  города: {', '.join(cfg['cities'])}")
        return

    conn = db.connect(args.db_path)
    # При --db-path .xlsx тоже уводим в отдельный файл рядом с этой базой —
    # иначе несколько параллельных запусков (изолированные --db-path, но общий
    # OUT_XLSX по умолчанию) наступили бы на тот же риск одновременной записи,
    # ради которого и заводился --db-path, только на уровне .xlsx, а не SQLite.
    if args.db_path:
        db_path_obj = Path(args.db_path)
        out_xlsx = db_path_obj.with_suffix(".xlsx")
        print(f"[!] Отдельная база: {db_path_obj} -> .xlsx: {out_xlsx} "
              f"(не data/leads.sqlite3 / out/*.xlsx по умолчанию)")
    else:
        out_xlsx = OUT_XLSX

    if args.export_only:
        all_leads = db.fetch_all(conn)
        by_region = {}
        for l in all_leads:
            by_region.setdefault(l["region"], []).append(l)
        out_xlsx.parent.mkdir(parents=True, exist_ok=True)
        export_xlsx(by_region, out_xlsx)
        print(f"Пересобрано из SQLite: {len(all_leads)} лидов -> {out_xlsx}")
        return

    if args.audit_only:
        all_leads = db.fetch_all(conn)
        by_region = {}
        for l in all_leads:
            by_region.setdefault(l["region"], []).append(l)
        for region_name, region_leads in by_region.items():
            print(f"\n--- Проверка на возможные дубли: {region_name} ---")
            format_report(find_possible_duplicates(region_leads))
        return

    if not args.regions:
        ap.error("нужен хотя бы один --region (см. --list-regions)")

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(sources) - set(ALL_SOURCES)
    if unknown:
        ap.error(f"неизвестные источники: {unknown}. Доступны: {ALL_SOURCES}")

    all_stats = {}
    for region_id in args.regions:
        region_cfg = get_region(region_id)
        if not region_cfg["confirmed"]:
            print(f"[!] Регион {region_cfg['name']!r} ещё не подтверждён владельцем — "
                  f"запускаю всё равно, т.к. явно указан в --region.")
        if args.cities:
            wanted = [c.strip() for c in args.cities.split(",") if c.strip()]
            known = set(region_cfg["cities"])
            missing = [c for c in wanted if c not in known]
            if missing:
                ap.error(f"--cities: {missing} нет в регионе {region_cfg['name']!r} "
                         f"(есть: {sorted(known)})")
            # копия конфига, а не правка REGIONS — глобальный конфиг не мутируем
            region_cfg = dict(region_cfg, cities=[c for c in region_cfg["cities"] if c in wanted])
            print(f"[!] --cities: только {region_cfg['cities']}")
        print(f"\n=== Регион: {region_cfg['name']} ({region_id}) | источники: {sources} ===")
        leads, stats = run_region(region_id, region_cfg, sources, conn)
        all_stats[region_cfg["name"]] = stats
        print(f"[{region_cfg['name']}] итог: собрано сырых записей={stats['raw_total']}, "
              f"прошли фильтр 'это стоматология'={stats['dental_filtered']}, "
              f"из них без сайта={stats['no_website']}, "
              f"с телефоном и/или email={stats['with_contact']}, "
              f"после слияния дублей -> лидов в базе={stats['final_leads']}")

    all_leads = db.fetch_all(conn)
    by_region = {}
    for l in all_leads:
        by_region.setdefault(l["region"], []).append(l)
    out_xlsx.parent.mkdir(parents=True, exist_ok=True)
    export_xlsx(by_region, out_xlsx)
    print(f"\n.xlsx обновлён: {out_xlsx} (все регионы, накопленные в этой SQLite: {len(all_leads)} лидов)")

    # Отдельный отчёт-проверка (не сливает автоматически, см. src/audit_dupes.py
    # и почему так — то же решение, что в audit_dupes_deep2.py у RUSIMEX)
    for region_id in args.regions:
        region_cfg = get_region(region_id)
        region_leads = by_region.get(region_cfg["name"], [])
        print(f"\n--- Проверка на возможные дубли: {region_cfg['name']} ---")
        format_report(find_possible_duplicates(region_leads))


if __name__ == "__main__":
    main()
