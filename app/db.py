"""Deterministic synthetic data, integer kopeks, monthly point-in-time snapshots.

This is a teaching dataset, NOT real bank data or a calibrated risk model.
Only this bootstrap/CLI module writes to SQLite. Analytics opens a read-only URI.
"""
from contextlib import contextmanager
from pathlib import Path
import calendar
import json
import math
import os
import random
import sqlite3
import tempfile

DATASET_VERSION = "synthetic-v1-seed42"
PRODUCTS = ("Потребительский", "Автокредит", "Ипотека", "Кредитная карта")
REGIONS = ("Центральный", "Северо-Западный", "Приволжский", "Уральский", "Сибирский")
SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE loans (
  loan_id TEXT PRIMARY KEY,
  product TEXT NOT NULL,
  region TEXT NOT NULL,
  risk_band TEXT NOT NULL CHECK(risk_band IN ('A','B','C','D')),
  annual_rate REAL NOT NULL CHECK(annual_rate BETWEEN 0 AND 1),
  origination_date TEXT NOT NULL,
  original_principal_kop INTEGER NOT NULL CHECK(original_principal_kop > 0)
);
CREATE TABLE snapshots (
  as_of TEXT NOT NULL,
  loan_id TEXT NOT NULL REFERENCES loans(loan_id),
  balance_kop INTEGER NOT NULL CHECK(balance_kop > 0),
  dpd INTEGER NOT NULL CHECK(dpd >= 0),
  pd_12m REAL NOT NULL CHECK(pd_12m BETWEEN 0 AND 1),
  lgd REAL NOT NULL CHECK(lgd BETWEEN 0 AND 1),
  PRIMARY KEY(as_of, loan_id)
);
CREATE INDEX idx_snapshots_loan ON snapshots(loan_id);
CREATE INDEX idx_loans_product_region ON loans(product, region);
"""


def month_ends():
    result = []
    for i in range(12):
        total = 2025 * 12 + 9 + i  # October 2025 .. September 2026, frozen demo window.
        year, month0 = divmod(total, 12)
        month = month0 + 1
        result.append(f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}")
    return result


@contextmanager
def read_db(path: Path):
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    try:
        yield con
    finally:
        con.close()


def seed_database(path: Path, n_loans=10000, seed=42, force=False):
    """Atomic replacement makes readers see either an old complete DB or the new one."""
    path = Path(path).resolve()
    if path.exists() and not force:
        with read_db(path) as con:
            version = con.execute("SELECT value FROM metadata WHERE key='dataset_version'").fetchone()
            if not version or version[0] != DATASET_VERSION:
                raise ValueError("Неизвестная версия БД. Используйте отдельный DATABASE_PATH или пересоздайте demo.")
        return
    if not 1 <= n_loans <= 50000:
        raise ValueError("n_loans must be between 1 and 50000")
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    dates = month_ends()
    loans, snapshots = [], []
    for i in range(n_loans):
        loan_id = f"SYN-{i + 1:06d}"
        product = rng.choices(PRODUCTS, weights=[42, 23, 22, 13])[0]
        region = rng.choices(REGIONS, weights=[30, 18, 24, 16, 12])[0]
        band = rng.choices(["A", "B", "C", "D"], weights=[28, 40, 24, 8])[0]
        ix = PRODUCTS.index(product)
        principal_rub = round(rng.lognormvariate(math.log([450000, 1350000, 5200000, 160000][ix]), .45))
        principal_kop = principal_rub * 100
        rate = round([.23, .18, .115, .29][ix] + .015 * "ABCD".index(band), 4)
        # A fixed panel: no new originations, no write-offs. Document the limitation.
        origination = f"2025-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}"
        loans.append((loan_id, product, region, band, rate, origination, principal_kop))
        base_pd = {"A": .008, "B": .022, "C": .060, "D": .145}[band]
        base_pd *= [.95, .70, .38, 1.25][ix]
        lgd = [.72, .43, .23, .85][ix]
        dpd = rng.choices([0, 15, 45, 75, 105], weights=[93, 3, 1.5, 1, 1.5])[0]
        balance = principal_kop
        for m, date in enumerate(dates):
            # Transparent planted stress: starting April, consumer/Volga deterioration.
            stress = 1 + max(0, m - 5) * (.18 if product == "Потребительский" else .025)
            if region == "Приволжский":
                stress *= 1 + max(0, m - 5) * .12
            if m:
                chance = rng.random()
                if dpd == 0:
                    if chance < min(.16, .006 + base_pd * .30 * stress):
                        dpd = rng.randint(1, 29)
                elif dpd < 90:
                    cure = max(.12, .40 / stress)
                    dpd = 0 if chance < cure else dpd + 30
                else:
                    dpd = 0 if chance < .025 else dpd + 30
                amort = [.013, .012, .004, .018][ix] if dpd == 0 else .001
                balance = max(1, round(balance * (1 - amort)))
            pd = 1.0 if dpd >= 90 else min(.65, base_pd * stress * (1 + dpd / 50))
            snapshots.append((date, loan_id, balance, dpd, round(pd, 6), lgd))
    fd, tmp = tempfile.mkstemp(prefix="portfolio-", suffix=".sqlite", dir=path.parent)
    os.close(fd)
    try:
        with sqlite3.connect(tmp) as con:
            con.executescript(SCHEMA)
            con.executemany("INSERT INTO loans VALUES (?,?,?,?,?,?,?)", loans)
            con.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?)", snapshots)
            meta = {"dataset_version": DATASET_VERSION, "seed": seed, "n_loans": n_loans,
                    "dates": dates, "synthetic": True, "currency": "RUB", "panel": "fixed"}
            con.executemany("INSERT INTO metadata VALUES (?,?)", [(k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v) for k, v in meta.items()])
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
