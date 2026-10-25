"""Read-only, parameterized analytics. LLMs NEVER calculate portfolio metrics here."""
from pathlib import Path
from datetime import date
from functools import lru_cache
from .db import read_db, DATASET_VERSION

DIMENSIONS = {"product": "product", "region": "region", "risk_band": "risk_band"}
METRICS = {"npl90_ratio", "balance_rub", "npl90_balance_rub", "el_ratio"}
BASE = " FROM snapshots s JOIN loans l ON l.loan_id=s.loan_id "
AGG = """COUNT(*) AS loan_count,
 COALESCE(SUM(balance_kop),0) AS balance_kop,
 COALESCE(SUM(CASE WHEN dpd>=1 THEN balance_kop ELSE 0 END),0) AS dpd1_kop,
 COALESCE(SUM(CASE WHEN dpd>=30 THEN balance_kop ELSE 0 END),0) AS dpd30_kop,
 COALESCE(SUM(CASE WHEN dpd>=60 THEN balance_kop ELSE 0 END),0) AS dpd60_kop,
 COALESCE(SUM(CASE WHEN dpd>=90 THEN balance_kop ELSE 0 END),0) AS npl90_kop,
 COALESCE(SUM(CASE WHEN dpd>=90 THEN 1 ELSE 0 END),0) AS npl90_count,
 COALESCE(SUM(balance_kop * annual_rate),0) AS weighted_rate,
 COALESCE(SUM(balance_kop * pd_12m * lgd),0) AS el_kop"""


def ratio(numerator, denominator):
    return round(numerator / denominator, 8) if denominator else None


def metrics(row):
    b = row["balance_kop"]
    return {
        "loan_count": row["loan_count"], "balance_rub": round(b / 100, 2),
        "npl90_balance_rub": round(row["npl90_kop"] / 100, 2), "npl90_count": row["npl90_count"],
        "npl90_ratio": ratio(row["npl90_kop"], b),
        "npl90_count_ratio": ratio(row["npl90_count"], row["loan_count"]),
        "dpd1_ratio": ratio(row["dpd1_kop"], b), "dpd30_ratio": ratio(row["dpd30_kop"], b),
        "dpd60_ratio": ratio(row["dpd60_kop"], b),
        "weighted_rate": ratio(row["weighted_rate"], b),
        "el_rub": round(row["el_kop"] / 100, 2), "el_ratio": ratio(row["el_kop"], b),
    }


class Analytics:
    def __init__(self, path: Path):
        self.path = Path(path)

    @lru_cache(maxsize=8)
    def context(self):
        with read_db(self.path) as con:
            dates = [r[0] for r in con.execute("SELECT DISTINCT as_of FROM snapshots ORDER BY as_of")]
            cats = {d: [r[0] for r in con.execute(f"SELECT DISTINCT {d} FROM loans ORDER BY {d}")] for d in DIMENSIONS}
            count = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
        return {"dataset": DATASET_VERSION, "synthetic": True, "currency": "RUB", "dates": dates,
                "latest_date": dates[-1], "loan_count": count, "filters": cats,
                "ratio_unit": "fraction: 0.05 means 5%", "delta_unit": "percentage_points",
                "npl_definition": "Учебный proxy: DPD >= 90; не регуляторное определение NPL",
                "el_definition": "Учебный EL = sum(balance * pd_12m * lgd); не IFRS 9 ECL",
                "panel": "Фиксированная когорта, без новых выдач и списаний; даты — срезы, не потоки"}

    def _date(self, value):
        dates = self.context()["dates"]
        if value is None:
            return dates[-1]
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError("Дата должна иметь формат YYYY-MM-DD") from None
        if value not in dates:
            raise ValueError(f"Нет среза {value}. Доступны: {', '.join(dates)}")
        return value

    def _where(self, as_of, product=None, region=None, risk_band=None):
        selected = self._date(as_of)
        clauses, params = ["s.as_of=?"], [selected]
        for key, value in {"product": product, "region": region, "risk_band": risk_band}.items():
            if value is not None:
                if value not in self.context()["filters"][key]:
                    raise ValueError(f"Неизвестный {key}: {value}")
                clauses.append(f"l.{key}=?")
                params.append(value)
        return " WHERE " + " AND ".join(clauses), params

    def _result(self, payload, as_of=None, **filters):
        return {"source": "SQLite / synthetic-v1", "dataset": DATASET_VERSION, "synthetic": True,
                "as_of": as_of, "filters": {k: v for k, v in filters.items() if v is not None},
                "currency": "RUB", **payload}

    def summary(self, as_of=None, **filters):
        where, params = self._where(as_of, **filters)
        with read_db(self.path) as con:
            row = con.execute("SELECT " + AGG + BASE + where, params).fetchone()
        return self._result({"metrics": metrics(row), "empty": row["loan_count"] == 0}, params[0], **filters)

    def delinquency(self, as_of=None, **filters):
        where, params = self._where(as_of, **filters)
        case = "CASE WHEN dpd=0 THEN '0' WHEN dpd<30 THEN '1–29' WHEN dpd<60 THEN '30–59' WHEN dpd<90 THEN '60–89' ELSE '90+' END"
        with read_db(self.path) as con:
            rows = con.execute(f"SELECT {case} AS bucket, COUNT(*) AS n, SUM(balance_kop) AS balance" + BASE + where + " GROUP BY bucket", params).fetchall()
        found = {r["bucket"]: r for r in rows}
        total = sum(r["balance"] for r in rows)
        buckets = []
        for b in ["0", "1–29", "30–59", "60–89", "90+"]:
            r = found.get(b)
            balance = r["balance"] if r else 0
            buckets.append({"bucket": b, "loan_count": r["n"] if r else 0,
                            "balance_rub": round(balance / 100, 2), "balance_share": ratio(balance, total)})
        return self._result({"buckets": buckets, "balance_rub": round(total / 100, 2)}, params[0], **filters)

    def trend(self, start_date=None, end_date=None, **filters):
        ctx = self.context()
        start = self._date(start_date) if start_date else ctx["dates"][0]
        end = self._date(end_date)
        if start > end:
            raise ValueError("start_date должен быть не позже end_date")
        points = []
        for d in ctx["dates"]:
            if start <= d <= end:
                points.append({"as_of": d, **self.summary(d, **filters)["metrics"]})
        delta = None
        if points and points[0]["npl90_ratio"] is not None and points[-1]["npl90_ratio"] is not None:
            delta = round((points[-1]["npl90_ratio"] - points[0]["npl90_ratio"]) * 100, 6)
        return self._result({"start_date": start, "end_date": end, "points": points, "npl90_delta_pp": delta}, end, **filters)

    def segments(self, dimension="product", metric="npl90_ratio", as_of=None, **filters):
        if dimension not in DIMENSIONS or metric not in METRICS:
            raise ValueError("Недопустимое измерение или метрика")
        where, params = self._where(as_of, **filters)
        with read_db(self.path) as con:
            rows = con.execute(f"SELECT l.{DIMENSIONS[dimension]} AS segment, " + AGG + BASE + where + f" GROUP BY l.{DIMENSIONS[dimension]}", params).fetchall()
        total = sum(r["balance_kop"] for r in rows)
        segments = [{"segment": r["segment"], **metrics(r), "portfolio_share": ratio(r["balance_kop"], total)} for r in rows]
        segments.sort(key=lambda r: r[metric] if r[metric] is not None else -1, reverse=True)
        return self._result({"dimension": dimension, "sort_metric": metric, "segments": segments}, params[0], **filters)

    def top_segments(self, dimension="region", limit=5, min_loans=30, as_of=None, **filters):
        result = self.segments(dimension, "npl90_ratio", as_of, **filters)
        result["segments"] = [s for s in result["segments"] if s["loan_count"] >= min_loans][:limit]
        result["min_loans"] = min_loans
        result["ranking_note"] = "Рейтинг по доле остатка DPD 90+, не по размеру потерь; малые сегменты исключены"
        return result

    def drivers(self, start_date, end_date, dimension="product", **filters):
        """Exact symmetric decomposition: Δ(w*r)=avg(w)*Δr + avg(r)*Δw.

        Attribution is an accounting identity, NOT evidence of causation.
        """
        start, end = self._date(start_date), self._date(end_date)
        if start >= end:
            raise ValueError("Для разложения start_date должен быть раньше end_date")
        if dimension not in DIMENSIONS:
            raise ValueError("Недопустимое измерение")
        def profile(snapshot):
            where, params = self._where(snapshot, **filters)
            with read_db(self.path) as con:
                rows = con.execute(f"SELECT l.{DIMENSIONS[dimension]} AS segment, " + AGG + BASE + where + f" GROUP BY l.{DIMENSIONS[dimension]}", params).fetchall()
            total = sum(r["balance_kop"] for r in rows)
            # Keep full precision until final display rounding; do not decompose
            # previously rounded dashboard ratios.
            return {r["segment"]: {"portfolio_share": r["balance_kop"] / total,
                    "npl90_ratio": r["npl90_kop"] / r["balance_kop"]} for r in rows}
        left, right = profile(start), profile(end)
        if not left or not right:
            raise ValueError("Недостаточно данных для сравнения")
        rows = []
        for name in sorted(left.keys() | right.keys()):
            old, new = left.get(name, {}), right.get(name, {})
            w0, w1 = old.get("portfolio_share", 0) or 0, new.get("portfolio_share", 0) or 0
            r0, r1 = old.get("npl90_ratio", 0) or 0, new.get("npl90_ratio", 0) or 0
            risk = ((w0 + w1) / 2) * (r1 - r0) * 100
            mix = ((r0 + r1) / 2) * (w1 - w0) * 100
            rows.append({"segment": name, "start_npl90_ratio": r0, "end_npl90_ratio": r1,
                         "start_weight": w0, "end_weight": w1,
                         "within_segment_pp": round(risk, 6), "mix_pp": round(mix, 6),
                         "total_contribution_pp": round(risk + mix, 6)})
        rows.sort(key=lambda x: x["total_contribution_pp"], reverse=True)
        s0, s1 = (sum(r["portfolio_share"] * r["npl90_ratio"] for r in data.values()) for data in [left, right])
        return self._result({"start_date": start, "end_date": end, "dimension": dimension,
                            "npl90_delta_pp": round((s1 - s0) * 100, 6), "contributions": rows,
                            "caveat": "Математическое разложение изменения, не установление причин"}, end, **filters)

    def stress(self, pd_multiplier=1.5, lgd_add_pp=5.0, as_of=None, **filters):
        if not 1 <= pd_multiplier <= 5 or not 0 <= lgd_add_pp <= 50:
            raise ValueError("PD multiplier: 1..5; LGD addition: 0..50 percentage points")
        where, params = self._where(as_of, **filters)
        query = "SELECT SUM(balance_kop) AS b, SUM(balance_kop*pd_12m*lgd) AS base, SUM(balance_kop*MIN(1.0,pd_12m*?)*MIN(1.0,lgd+?)) AS stressed" + BASE + where
        with read_db(self.path) as con:
            r = con.execute(query, [pd_multiplier, lgd_add_pp / 100, *params]).fetchone()
        if r["b"] is None:
            raise ValueError("Пустая выборка для сценария")
        return self._result({"pd_multiplier": pd_multiplier, "lgd_add_pp": lgd_add_pp,
                            "base_el_rub": round(r["base"] / 100, 2), "stressed_el_rub": round(r["stressed"] / 100, 2),
                            "delta_el_rub": round((r["stressed"] - r["base"]) / 100, 2),
                            "base_el_ratio": ratio(r["base"], r["b"]), "stressed_el_ratio": ratio(r["stressed"], r["b"]),
                            "caveat": "What-if, не прогноз. PD и LGD ограничены единицей; EAD фиксирован. Не IFRS 9 ECL."}, params[0], **filters)

    def quality(self):
        with read_db(self.path) as con:
            count = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            loans = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
            invalid = con.execute("SELECT COUNT(*) FROM snapshots WHERE balance_kop<=0 OR dpd<0 OR pd_12m NOT BETWEEN 0 AND 1 OR lgd NOT BETWEEN 0 AND 1").fetchone()[0]
            orphans = con.execute("SELECT COUNT(*) FROM snapshots s LEFT JOIN loans l ON s.loan_id=l.loan_id WHERE l.loan_id IS NULL").fetchone()[0]
            dates = con.execute("SELECT as_of,COUNT(*) AS n FROM snapshots GROUP BY as_of ORDER BY as_of").fetchall()
            integrity = con.execute("PRAGMA quick_check").fetchone()[0]
        return {"dataset": DATASET_VERSION, "synthetic": True, "snapshots": count, "unique_loans": loans,
                "invalid_rows": invalid, "orphan_rows": orphans, "duplicate_rows": 0,
                "duplicate_note": "Дубликаты запрещены PRIMARY KEY (as_of, loan_id)",
                "months": len(dates), "complete_panel": all(r["n"] == loans for r in dates),
                "sqlite_integrity": integrity, "passed": invalid == 0 and orphans == 0 and integrity == "ok",
                "limitations": ["Данные синтетические", "PD/LGD не откалиброваны", "Нет новых выдач, списаний и реального календаря платежей"]}
