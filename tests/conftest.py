from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.db import SCHEMA, DATASET_VERSION

@pytest.fixture
def tiny_db(tmp_path):
    p = tmp_path / "tiny.sqlite"
    with sqlite3.connect(p) as con:
        con.executescript(SCHEMA)
        con.execute("INSERT INTO metadata VALUES ('dataset_version',?)", (DATASET_VERSION,))
        con.executemany("INSERT INTO loans VALUES (?,?,?,?,?,?,?)", [
            ("L1", "Потребительский", "Центральный", "A", .1, "2025-01-01", 100000),
            ("L2", "Потребительский", "Приволжский", "B", .2, "2025-01-01", 200000),
            ("L3", "Ипотека", "Центральный", "C", .3, "2025-01-01", 300000),
            ("L4", "Автокредит", "Уральский", "D", .4, "2025-01-01", 400000),
        ])
        rows = [("2026-08-31", "L1", 100000, 0, .1, .5),
                ("2026-08-31", "L2", 200000, 30, .2, .4),
                ("2026-08-31", "L3", 300000, 0, .3, .3),
                ("2026-08-31", "L4", 400000, 90, 1, .2),
                ("2026-09-30", "L1", 90000, 0, .1, .5),
                ("2026-09-30", "L2", 190000, 90, 1, .4),
                ("2026-09-30", "L3", 290000, 60, .3, .3),
                ("2026-09-30", "L4", 390000, 120, 1, .2)]
        con.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?)", rows)
    return p

@pytest.fixture
def settings(tiny_db):
    return replace(Settings.from_env(), db_path=tiny_db, mode="demo", access_token="",
                   llm_api_key="", llm_model="", requests_per_minute=100, seed_loans=100)
