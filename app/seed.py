from .db import execute, query
from .prompts import DAOIST_SEED


def seed_daoist_cards():
    if query("SELECT COUNT(*) AS c FROM daoist_cards")[0]["c"] > 0:
        return
    for core, src, scen in DAOIST_SEED:
        execute(
            "INSERT INTO daoist_cards (core_idea, source_ref, applicable_scenario, created_at) "
            "VALUES (?,?,?,datetime('now'))",
            (core, src, scen),
        )
