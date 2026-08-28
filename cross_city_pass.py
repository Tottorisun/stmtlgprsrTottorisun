# -*- coding: utf-8 -*-
"""
Межгородской проход по дублям ПОСЛЕ слияния параллельных баз (merge_parallel.py).

Зачем: dedupe_key включает город, поэтому одна и та же клиника, попавшая в
выдачу двух городов (реальный найденный случай — поиск Яндекса по Дзержинску
подмешал клиники Нижнего Новгорода), автослиянием не ловится.

Что делает (консервативно, в духе src/audit_dupes.py — «смотреть глазами»):

1. ПОДТВЕРЖДЁННЫЕ артефакты Дзержинска: строки с city='Дзержинск', у которых
   адрес — Нижний Новгород И есть общий телефон с существующей строкой
   Нижнего Новгорода. Это та же клиника дважды -> телефоны/источники
   объединяются в строку НН, артефакт удаляется. Каждый случай печатается.
   Если телефон артефакта совпадает со строками НЕСКОЛЬКИХ клиник НН —
   неоднозначно, НЕ сливаем, только помечаем.

2. Прочие межгородские совпадения телефона В ПРЕДЕЛАХ ОДНОГО региона:
   НИЧЕГО не удаляется — в notes каждой строки группы дописывается пометка
   «возможный межгородской дубль...». Общий номер у 3+ разных названий —
   типичный колл-центр сети (см. src/dedup.py), это отражается в пометке.

3. Перекос «город карточки != город в адресе» для двух известных случаев
   районного «затекания» выдачи (Дзержинск<-Нижний Новгород,
   Всеволожск<-Санкт-Петербург): не удаляется, только пометка в notes.
   (Всеволожский запрос захватил весь район: 20 из 32 строк — питерские
   адреса; телефонных совпадений со строками СПб нет, т.к. по самому СПб
   собрано только 25 карточек.)

Повторный запуск безопасен: пометки не дублируются, подтверждённые артефакты
уже удалены.

Запуск:
    python cross_city_pass.py            # применить
    python cross_city_pass.py --dry-run  # только показать, ничего не менять
"""
import argparse
import sqlite3
import sys
import io
from collections import defaultdict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.db import _merge_semicolon_list
from src.normalize import name_key

DB = ROOT / "data" / "leads.sqlite3"

# Известные случаи «поиск по городу-спутнику захватил мегаполис»:
# city карточки -> город, чьё имя в адресе выдаёт артефакт.
CITY_BLEED = {
    ("Нижегородская область", "Дзержинск"): "Нижний Новгород",
    ("Ленинградская область", "Всеволожск"): "Санкт-Петербург",
}

MARK_DUP = "возможный межгородской дубль"
MARK_BLEED = "город карточки"


def digits(p):
    return "".join(ch for ch in str(p or "") if ch.isdigit())


def phones_of(s):
    return {d for d in (digits(x) for x in str(s or "").split(";")) if len(d) >= 10}


def add_note(conn, key, note, dry):
    row = conn.execute("SELECT notes FROM leads WHERE dedupe_key=?", (key,)).fetchone()
    old = (row[0] or "").strip() if row else ""
    if note in old:
        return False  # уже помечено прошлым запуском
    new = f"{old} | {note}" if old else note
    if not dry:
        conn.execute("UPDATE leads SET notes=? WHERE dedupe_key=?", (new, key))
    return True


def fetch(conn):
    cur = conn.execute(
        "SELECT dedupe_key, org_name, region, city, address, phone, source, source_url, email "
        "FROM leads")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def merge_dzerzhinsk_artifacts(conn, dry):
    """Шаг 1: подтверждённые артефакты Дзержинск/НН — слить в строку НН, артефакт удалить."""
    region, art_city, real_city = "Нижегородская область", "Дзержинск", "Нижний Новгород"
    rows = [r for r in fetch(conn) if r["region"] == region]
    real_by_phone = defaultdict(set)
    real_rows = {}
    for r in rows:
        if r["city"] == real_city:
            real_rows[r["dedupe_key"]] = r
            for p in phones_of(r["phone"]):
                real_by_phone[p].add(r["dedupe_key"])

    merged, ambiguous = [], []
    for r in rows:
        if r["city"] != art_city or real_city not in (r["address"] or ""):
            continue
        targets = set()
        for p in phones_of(r["phone"]):
            targets |= real_by_phone.get(p, set())
        if not targets:
            continue  # нет телефонного подтверждения — шаг 3 только пометит
        if len(targets) > 1:
            ambiguous.append((r, sorted(targets)))
            continue
        target_key = targets.pop()
        t = real_rows[target_key]
        upd = {
            "phone": _merge_semicolon_list(t["phone"], r["phone"]),
            "source": _merge_semicolon_list(t["source"], r["source"]),
            "source_url": _merge_semicolon_list(t["source_url"], r["source_url"]),
            "email": t["email"] or r["email"],
        }
        if not dry:
            conn.execute(
                "UPDATE leads SET phone=?, source=?, source_url=?, email=? WHERE dedupe_key=?",
                (upd["phone"], upd["source"], upd["source_url"], upd["email"], target_key))
            conn.execute("DELETE FROM leads WHERE dedupe_key=?", (r["dedupe_key"],))
        add_note(conn, target_key,
                 f"поглощён артефакт дзержинской выдачи Яндекса: {r['dedupe_key']}", dry)
        merged.append((r, target_key))

    print(f"--- Шаг 1: подтверждённые артефакты {art_city} -> {real_city} ---")
    for r, tk in merged:
        print(f"  СЛИТ И УДАЛЁН: {r['dedupe_key']!r}")
        print(f"    адрес: {r['address'][:70]} | тел: {r['phone']}")
        print(f"    -> в строку НН: {tk!r}")
    for r, tks in ambiguous:
        note = (f"{MARK_DUP}: адрес НН, телефон совпадает с несколькими строками НН "
                f"({', '.join(tks)}) — слить вручную")
        add_note(conn, r["dedupe_key"], note, dry)
        print(f"  НЕОДНОЗНАЧНО (только пометка): {r['dedupe_key']!r} -> {tks}")
    print(f"  итого слито и удалено: {len(merged)}, неоднозначных (помечено): {len(ambiguous)}")
    return len(merged)


def mark_cross_city_phones(conn, dry):
    """Шаг 2: общий телефон в разных городах одного региона — только пометки."""
    rows = fetch(conn)
    by_region_phone = defaultdict(list)
    for r in rows:
        for p in phones_of(r["phone"]):
            by_region_phone[(r["region"], p)].append(r)

    groups = {}  # frozenset(keys) -> (phone, rows) — одна пометка на группу строк
    for (region, p), lst in by_region_phone.items():
        if len({r["city"] for r in lst}) < 2:
            continue
        ks = frozenset(r["dedupe_key"] for r in lst)
        groups.setdefault(ks, (p, lst))

    print(f"\n--- Шаг 2: общий телефон в разных городах одного региона (только пометки) ---")
    marked = 0
    for ks, (p, lst) in sorted(groups.items(), key=lambda kv: kv[1][1][0]["region"]):
        names = {name_key(r["org_name"]) for r in lst}
        network_hint = ("; 3+ разных названий с одним номером — похоже на общий "
                        "колл-центр сети, а не дубль" if len(names) >= 3 else "")
        print(f"  [{lst[0]['region']}] тел +{p}:")
        for r in lst:
            others = [f"{x['dedupe_key']} ({x['city']})" for x in lst
                      if x["dedupe_key"] != r["dedupe_key"]]
            note = (f"{MARK_DUP}: тот же телефон +{p}, что у {'; '.join(others)}"
                    f"{network_hint} — проверить вручную")
            if add_note(conn, r["dedupe_key"], note, dry):
                marked += 1
            print(f"    {r['org_name'][:38]:40s} | {r['city']:18s} | {r['address'][:45]}")
    print(f"  групп: {len(groups)}, строк помечено: {marked}")
    return groups


def mark_city_bleed(conn, dry):
    """Шаг 3: город карточки != город в адресе (известные случаи) — только пометки."""
    rows = fetch(conn)
    print(f"\n--- Шаг 3: адрес выдаёт другой город (только пометки) ---")
    marked = 0
    for (region, art_city), real_city in CITY_BLEED.items():
        hits = [r for r in rows
                if r["region"] == region and r["city"] == art_city
                and real_city in (r["address"] or "")]
        for r in hits:
            note = (f"{MARK_BLEED} — {art_city}, но адрес — {real_city}: артефакт "
                    f"районного поиска Яндекса, город/регион проверить вручную")
            if add_note(conn, r["dedupe_key"], note, dry):
                marked += 1
        print(f"  {region} / {art_city} -> адрес {real_city}: {len(hits)} строк")
        for r in hits:
            print(f"    {r['org_name'][:38]:40s} | {r['address'][:60]}")
    print(f"  строк помечено: {marked}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="показать, ничего не менять")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    before = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    deleted = merge_dzerzhinsk_artifacts(conn, args.dry_run)
    mark_cross_city_phones(conn, args.dry_run)
    mark_city_bleed(conn, args.dry_run)
    if not args.dry_run:
        conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    print(f"\nЛидов: {before} -> {after} (удалено подтверждённых артефактов: {deleted})"
          + (" [DRY RUN — база не менялась]" if args.dry_run else ""))
    conn.close()


if __name__ == "__main__":
    main()
