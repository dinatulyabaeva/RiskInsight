import sqlite3
import pytest
from pydantic import ValidationError
from app.analytics import Analytics
from app.db import seed_database, read_db, month_ends
from app.tool_registry import execute_tool


def test_summary_hand_calculation(tiny_db):
    m = Analytics(tiny_db).summary()["metrics"]
    assert m["balance_rub"] == 9600
    assert m["loan_count"] == 4
    assert m["npl90_balance_rub"] == 5800
    assert m["npl90_ratio"] == pytest.approx(5800/9600)
    assert m["npl90_count_ratio"] == .5
    assert m["el_rub"] == 1846
    assert m["weighted_rate"] == pytest.approx(2900/9600)
    assert m["dpd1_ratio"] >= m["dpd30_ratio"] >= m["dpd60_ratio"] >= m["npl90_ratio"]


def test_buckets_reconcile(tiny_db):
    b = Analytics(tiny_db).delinquency()["buckets"]
    assert sum(x["balance_rub"] for x in b) == 9600
    assert sum(x["loan_count"] for x in b) == 4
    assert sum(x["balance_share"] for x in b) == pytest.approx(1)
    assert b[-1]["balance_rub"] == 5800


def test_date_range_and_percentage_points(tiny_db):
    t = Analytics(tiny_db).trend()
    assert len(t["points"]) == 2
    assert t["points"][0]["npl90_ratio"] == .4
    assert t["npl90_delta_pp"] == pytest.approx(20.416667, abs=1e-6)


def test_empty_is_null_not_zero(tiny_db):
    s = Analytics(tiny_db).summary(product="Ипотека", region="Приволжский")
    assert s["empty"]
    assert s["metrics"]["loan_count"] == 0
    assert s["metrics"]["npl90_ratio"] is None
    assert s["metrics"]["weighted_rate"] is None


def test_filters_not_ignored(tiny_db):
    s = Analytics(tiny_db).summary(product="Потребительский", region="Приволжский")
    assert s["metrics"]["balance_rub"] == 1900
    assert s["metrics"]["npl90_ratio"] == 1
    assert s["filters"]["region"] == "Приволжский"


@pytest.mark.parametrize("value", ["2026-02-31", "2026-01-31", "latest", "2026-10-21", "' OR 1=1 --"])
def test_invalid_date_not_silently_substituted(tiny_db, value):
    with pytest.raises(ValueError):
        Analytics(tiny_db).summary(value)


def test_reversed_period(tiny_db):
    with pytest.raises(ValueError):
        Analytics(tiny_db).trend("2026-09-30", "2026-08-31")


def test_unknown_category_error(tiny_db):
    with pytest.raises(ValueError):
        Analytics(tiny_db).summary(region="несуществующий")


def test_segments_reconcile(tiny_db):
    s = Analytics(tiny_db).segments()["segments"]
    assert sum(x["balance_rub"] for x in s) == 9600
    assert sum(x["npl90_balance_rub"] for x in s) == 5800
    assert s[0]["segment"] == "Автокредит"


def test_small_segments_excluded(tiny_db):
    assert Analytics(tiny_db).top_segments(min_loans=30)["segments"] == []
    assert len(Analytics(tiny_db).top_segments(min_loans=1, limit=1)["segments"]) == 1


@pytest.mark.parametrize("dim", ["product", "region", "risk_band"])
def test_decomposition_adds_up(tiny_db, dim):
    result = Analytics(tiny_db).drivers("2026-08-31", "2026-09-30", dim)
    rows = result["contributions"]
    assert sum(x["total_contribution_pp"] for x in rows) == pytest.approx(result["npl90_delta_pp"], abs=3e-6)
    for row in rows:
        assert row["total_contribution_pp"] == pytest.approx(row["within_segment_pp"] + row["mix_pp"], abs=1.1e-6)


def test_stress_identity(tiny_db):
    r = Analytics(tiny_db).stress(1, 0)
    assert r["base_el_rub"] == r["stressed_el_rub"] == 1846
    assert r["delta_el_rub"] == 0


def test_stress_caps_and_monotonicity(tiny_db):
    a = Analytics(tiny_db)
    small, large = a.stress(1.5, 5), a.stress(5, 50)
    assert 1846 <= small["stressed_el_rub"] <= large["stressed_el_rub"] <= 9600


@pytest.mark.parametrize("name,args", [
    ("analyze_segment", {"dimension": "product; DROP TABLE loans;--"}),
    ("get_portfolio_summary", {"sql": "DELETE FROM loans"}),
    ("run_stress_scenario", {"pd_multiplier": 100}),
    ("run_stress_scenario", {"lgd_add_pp": -5}),
    ("run_stress_scenario", {"pd_multiplier": float('nan')}),
    ("get_top_risk_segments", {"limit": 1000}),
])
def test_tool_contract_rejects_invalid(tiny_db, name, args):
    with pytest.raises(ValidationError):
        execute_tool(Analytics(tiny_db), name, args)


def test_database_read_only(tiny_db):
    with read_db(tiny_db) as con:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM loans")


def test_quality(tiny_db):
    r = Analytics(tiny_db).quality()
    assert r["passed"] and r["complete_panel"]
    assert r["snapshots"] == 8


def test_seed_reproducible(tmp_path):
    a, b = tmp_path / 'a.db', tmp_path / 'b.db'
    seed_database(a, 100)
    seed_database(b, 100)
    assert Analytics(a).summary() == Analytics(b).summary()
    assert Analytics(a).quality()["snapshots"] == 1200
    assert len(month_ends()) == 12
    assert month_ends()[-1] == "2026-09-30"
