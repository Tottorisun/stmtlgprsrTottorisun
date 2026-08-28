# -*- coding: utf-8 -*-
"""
SQLite — основное хранилище. .xlsx (export_xlsx.py) генерируется ИЗ него,
а не наоборот, так что база всегда содержит полную историю (в т.ч. по
регионам, собранным раньше), а .xlsx можно пересобрать в любой момент.

На повторный запуск того же региона: лид с уже существующим dedupe_key не
дублируется, а дозаполняется (новый источник, новый телефон и т.п.) —
см. upsert_lead().
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

from .models import FIELDS

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "leads.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT UNIQUE NOT NULL,
    org_name TEXT NOT NULL,
    category TEXT,
    region TEXT NOT NULL,
    city TEXT,
    address TEXT,
    phone TEXT,
    email TEXT,
    has_website INTEGER NOT NULL DEFAULT 0,
    website TEXT DEFAULT '',
    source TEXT,
    source_url TEXT,
    scraped_at TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_region ON leads(region);
"""

# Колонки, дозаводимые на существующих базах (созданных до появления колонки в
# схеме). ALTER TABLE ADD COLUMN в SQLite дешёвый и не переписывает таблицу.
_MIGRATIONS = [
    ("website", "TEXT DEFAULT ''"),
]


def _migrate(conn):
    """Дозавести недостающие колонки на базе, созданной старой версией схемы.
    Аддитивно: существующие данные не трогаются, новая колонка получает DEFAULT.
    Нужно, потому что CREATE TABLE IF NOT EXISTS не меняет уже существующую
    таблицу — база no-site 1905 лидов остаётся валидной, просто получает
    пустую website."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    for col, decl in _MIGRATIONS:
        if col not in have:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {decl}")


def connect(db_path=None):
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _merge_semicolon_list(old, new):
    parts = [p.strip() for p in str(old or "").split(";") if p.strip()]
    for p in [p.strip() for p in str(new or "").split(";") if p.strip()]:
        if p not in parts:
            parts.append(p)
    return "; ".join(parts)


def upsert_lead(conn, lead: dict):
    """lead: dict с ключами из models.FIELDS. Возвращает 'inserted' | 'updated'."""
    cur = conn.execute("SELECT * FROM leads WHERE dedupe_key = ?", (lead["dedupe_key"],))
    row = cur.fetchone()
    if row is None:
        cols = ", ".join(FIELDS)
        qs = ", ".join("?" for _ in FIELDS)
        # .get: лид из режима no-site не содержит website — подставляем "".
        conn.execute(f"INSERT INTO leads ({cols}) VALUES ({qs})",
                     [lead.get(f, "") for f in FIELDS])
        return "inserted"

    cols = [d[0] for d in cur.description]
    existing = dict(zip(cols, row))
    merged = dict(existing)
    merged["phone"] = _merge_semicolon_list(existing["phone"], lead["phone"])
    merged["source"] = _merge_semicolon_list(existing["source"], lead["source"])
    merged["source_url"] = _merge_semicolon_list(existing["source_url"], lead["source_url"])
    merged["email"] = existing["email"] or lead["email"]
    merged["address"] = existing["address"] or lead["address"]
    merged["category"] = existing["category"] or lead["category"]
    # website: заполняем, если у существующей записи пусто (первый источник, где
    # сайт реально нашёлся, побеждает). .get — на случай базы без колонки/лида
    # без ключа (старый режим no-site website не проставляет).
    merged["website"] = existing.get("website") or lead.get("website") or ""
    merged["scraped_at"] = lead["scraped_at"]
    conn.execute(
        "UPDATE leads SET phone=?, source=?, source_url=?, email=?, address=?, "
        "category=?, website=?, scraped_at=? WHERE dedupe_key=?",
        (merged["phone"], merged["source"], merged["source_url"], merged["email"],
         merged["address"], merged["category"], merged["website"],
         merged["scraped_at"], lead["dedupe_key"]))
    return "updated"


def fetch_region(conn, region_name):
    cur = conn.execute(f"SELECT {', '.join(FIELDS)} FROM leads WHERE region = ? ORDER BY city, org_name",
                        (region_name,))
    return [dict(zip(FIELDS, row)) for row in cur.fetchall()]


def fetch_all(conn):
    cur = conn.execute(f"SELECT {', '.join(FIELDS)} FROM leads ORDER BY region, city, org_name")
    return [dict(zip(FIELDS, row)) for row in cur.fetchall()]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
