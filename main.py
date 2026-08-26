# -*- coding: utf-8 -*-
"""
CLI: собрать лиды (стоматологии без сайта) по одному или нескольким регионам.

Примеры:
    python main.py --region tyumen
    python main.py --region tyumen --sources yandex_maps
    python main.py --region moscow --region krasnodar --sources yandex_maps,2gis
    python main.py --list-regions
    python main.py --export-only          # пересобрать .xlsx из уже накопленной SQLite, без сети

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
    args = ap.parse_args()

    if args.list_regions:
        for rid, cfg in REGIONS.items():
            flag = "подтверждён" if cfg["confirmed"] else "ПРЕДЛОЖЕН, требует подтверждения"
            print(f"{rid:12s} {cfg['name']:28s} [{flag}]  города: {', '.join(cfg['cities'])}")
        return

    conn = db.connect()

    if args.export_only:
        all_leads = db.fetch_all(conn)
        by_region = {}
        for l in all_leads:
            by_region.setdefault(l["region"], []).append(l)
        OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
        export_xlsx(by_region, OUT_XLSX)
        print(f"Пересобрано из SQLite: {len(all_leads)} лидов -> {OUT_XLSX}")
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
    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    export_xlsx(by_region, OUT_XLSX)
    print(f"\n.xlsx обновлён: {OUT_XLSX} (все регионы, накопленные в SQLite: {len(all_leads)} лидов)")


if __name__ == "__main__":
    main()
