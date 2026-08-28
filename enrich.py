# -*- coding: utf-8 -*-
"""
CLI обогащения лидов правовой формой (ИП / ООО / гос / прочее / неизвестно).

Отдельный от main.py вход — СОЗНАТЕЛЬНО: в момент написания параллельные
агенты гоняют main.py по своим регионам, и менять main.py/pipeline.py под
ними нельзя (аддитивное правило). Вся логика — в src/enrich_legal_form.py.

Примеры:
    python enrich.py                    # основная база data/leads.sqlite3
    python enrich.py --limit 20         # калибровочный прогон
    python enrich.py --offline-only     # только эвристики по названию, без сети
    python enrich.py --retry-errors     # переобработать лиды с ошибками
    python enrich.py --db-path data/parallel/ural.sqlite3
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.enrich_legal_form import main

if __name__ == "__main__":
    main()
