# -*- coding: utf-8 -*-
"""
Одноразовое слияние изолированных баз параллельного сбора в основную
data/leads.sqlite3 — тот самый «отдельный шаг после», обещанный в docstring
main.py про --db-path.

Семантика ТА ЖЕ, что при обычном сборе: src/db.py::upsert_lead — лид с уже
существующим dedupe_key не дублируется, а дозаполняется (телефоны/источники
объединяются). Регионы в базах не пересекаются, так что коллизий ключей
ожидается ~0; фактическое число печатается в отчёте.

Перед записью основная база копируется в data/backups/ с таймстампом
(data/ закрыт .gitignore — бэкап в git не попадёт).

Базы-источники в data/parallel/ ТОЛЬКО читаются.

Запуск:
    python merge_parallel.py            # слить все 5 баз
    python merge_parallel.py --dry-run  # только посчитать, ничего не писать
"""
import argparse
import shutil
import sqlite3
import sys
import io
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import db
from src.models import FIELDS

# Явный список финальных баз параллельного флота (НЕ glob: рядом лежит
# _test_cities.sqlite3 и профили Chrome, которые сливать нельзя).
PARALLEL_DBS = [
    ROOT / "data" / "parallel" / "moscow_oblast.sqlite3",
    ROOT / "data" / "parallel" / "volga.sqlite3",
    ROOT / "data" / "parallel" / "south_siberia.sqlite3",
    ROOT / "data" / "parallel" / "spb_lenoblast.sqlite3",
    ROOT / "data" / "parallel" / "ural.sqlite3",
]


def backup_main_db(main_path):
    backups = ROOT / "data" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = backups / f"leads_{stamp}.sqlite3"
    shutil.copy2(main_path, dst)
    return dst


def read_leads(path):
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)  # источник только читаем
    try:
        # Базы-источники могут быть созданы РАЗНЫМИ версиями схемы (напр. без
        # добавленной позже колонки website) — открыты read-only, дозавести
        # ALTER'ом нельзя. Берём пересечение FIELDS с фактическими колонками,
        # недостающие поля добираем как "" — merge остаётся работоспособным
        # на старых базах параллельного флота.
        have = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
        cols = [f for f in FIELDS if f in have]
        cur = conn.execute(f"SELECT {', '.join(cols)} FROM leads")
        out = []
        for row in cur.fetchall():
            rec = dict(zip(cols, row))
            for f in FIELDS:
                rec.setdefault(f, "")
            out.append(rec)
        return out
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="посчитать вставки/коллизии, ничего не записывая")
    args = ap.parse_args()

    main_path = ROOT / "data" / "leads.sqlite3"
    if not main_path.exists():
        sys.exit(f"Нет основной базы: {main_path}")
    missing = [p for p in PARALLEL_DBS if not p.exists()]
    if missing:
        sys.exit(f"Нет баз-источников: {missing}")

    if not args.dry_run:
        bak = backup_main_db(main_path)
        print(f"Бэкап основной базы: {bak}")

    conn = db.connect(main_path)
    before = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    print(f"В основной базе до слияния: {before} лидов")

    total_ins = total_upd = 0
    collisions = []
    for src_path in PARALLEL_DBS:
        leads = read_leads(src_path)
        ins = upd = 0
        for lead in leads:
            if args.dry_run:
                row = conn.execute("SELECT dedupe_key FROM leads WHERE dedupe_key=?",
                                   (lead["dedupe_key"],)).fetchone()
                if row:
                    upd += 1
                    collisions.append((src_path.name, lead["dedupe_key"]))
                else:
                    ins += 1
                continue
            res = db.upsert_lead(conn, lead)
            if res == "inserted":
                ins += 1
            else:
                upd += 1
                collisions.append((src_path.name, lead["dedupe_key"]))
        if not args.dry_run:
            conn.commit()   # коммит после каждой базы: упавший процесс теряет максимум одну
        total_ins += ins
        total_upd += upd
        print(f"  {src_path.name:28s} прочитано {len(leads):4d} -> вставлено {ins}, "
              f"слито с существующими {upd}")

    after = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    print(f"\nИтого: вставлено {total_ins}, коллизий dedupe_key (слито) {total_upd}")
    if collisions:
        print("Коллизии (лид уже был в основной базе, телефоны/источники объединены):")
        for src_name, key in collisions:
            print(f"  [{src_name}] {key}")
    print(f"В основной базе после слияния: {after} лидов "
          f"(ожидалось {before} + вставки {total_ins} = {before + total_ins})")
    if not args.dry_run and after != before + total_ins:
        print("[!] РАСХОЖДЕНИЕ СЧЁТЧИКОВ — проверить вручную")
    conn.close()


if __name__ == "__main__":
    main()
