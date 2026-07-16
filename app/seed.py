from .db import execute, query
from .prompts import DAOIST_SEED
from .classics_data import CLASSICS, CATEGORY_PREFIX, CATEGORY_ORDER


def seed_daoist_cards():
    if query("SELECT COUNT(*) AS c FROM daoist_cards")[0]["c"] > 0:
        return
    for core, src, scen in DAOIST_SEED:
        execute(
            "INSERT INTO daoist_cards (core_idea, source_ref, applicable_scenario, created_at) "
            "VALUES (?,?,?,datetime('now'))",
            (core, src, scen),
        )


def seed_classics():
    if query("SELECT COUNT(*) AS c FROM classics")[0]["c"] > 0:
        return
    # 按分类顺序编号,每段从 001 起
    counters = {cat: 0 for cat in CATEGORY_ORDER}
    for category, book, chapter, text, yi in CLASSICS:
        prefix = CATEGORY_PREFIX.get(category, "典")
        counters[category] = counters.get(category, 0) + 1
        code = f"{prefix}·{counters[category]:03d}"
        execute(
            "INSERT INTO classics (category, book, chapter, text, yi, code, created_at) "
            "VALUES (?,?,?,?,?,?,datetime('now'))",
            (category, book, chapter, text, yi, code),
        )
