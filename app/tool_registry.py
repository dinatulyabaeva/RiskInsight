"""Single source of truth for MCP tool schemas and executable contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .analytics import Analytics

class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Filters(Empty):
    product: str | None = Field(None, max_length=80, description="Точное значение из get_dataset_context.filters.product")
    region: str | None = Field(None, max_length=80, description="Точное значение из get_dataset_context.filters.region")
    risk_band: Literal["A", "B", "C", "D"] | None = None

class Snapshot(Filters):
    as_of: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Дата доступного среза; null = последний")

class Period(Filters):
    start_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")

class Segments(Snapshot):
    dimension: Literal["product", "region", "risk_band"] = "product"
    metric: Literal["npl90_ratio", "balance_rub", "npl90_balance_rub", "el_ratio"] = "npl90_ratio"

class Ranking(Snapshot):
    dimension: Literal["product", "region", "risk_band"] = "region"
    limit: int = Field(5, ge=1, le=20)
    min_loans: int = Field(30, ge=1, le=10000)

class Drivers(Filters):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    dimension: Literal["product", "region", "risk_band"] = "product"

class Stress(Snapshot):
    pd_multiplier: float = Field(1.5, ge=1, le=5, description="Множитель PD, не процентов")
    lgd_add_pp: float = Field(5, ge=0, le=50, description="Прибавка LGD в процентных пунктах")

# name: (contract, analytics method, description)
TOOLS = {
    "get_dataset_context": (Empty, "context", "Получить доступные даты, фильтры, определения метрик и ограничения. Вызывай первым для нового анализа."),
    "get_portfolio_summary": (Snapshot, "summary", "Объем портфеля, число кредитов, DPD 1+/30+/60+/90+, взвешенная ставка и учебный EL на одном срезе. Доли возвращаются 0..1."),
    "get_delinquency_distribution": (Snapshot, "delinquency", "Взаимоисключающие корзины DPD: 0, 1–29, 30–59, 60–89, 90+. Доли по остатку задолженности."),
    "get_npl_trend": (Period, "trend", "Помесячные показатели и изменение DPD90+ в процентных пунктах. Остатки разных дат нельзя суммировать."),
    "analyze_segment": (Segments, "segments", "Сравнить продукты, регионы или рейтинги по риску, объему и вкладу. Сортировка по выбранной метрике."),
    "get_top_risk_segments": (Ranking, "top_segments", "Топ сегментов по доле DPD90+ с отсечением малых сегментов, не причинный анализ."),
    "explain_npl_change": (Drivers, "drivers", "Разложить изменение доли DPD90+ на внутрисегментный риск и изменение структуры. Точное арифметическое разложение, НЕ доказательство причин."),
    "run_stress_scenario": (Stress, "stress", "What-if: умножить PD и добавить п.п. к LGD; PD/LGD <=1. EAD фиксирован. Возвращает учебные потери, НЕ прогноз и НЕ IFRS9 ECL."),
    "check_data_quality": (Empty, "quality", "Проверить полноту срезов, диапазоны, связи и целостность SQLite. Не проверяет реалистичность синтетических PD/LGD."),
}

METHODOLOGY = """RiskInsight / учебная методология v1
Все данные синтетические. Валюта RUB. Деньги хранятся в копейках.
DPD = число дней просрочки. NPL90 proxy = остаток по кредитам DPD>=90 / весь остаток.
Это не регуляторное определение NPL: не учитываются unlikely-to-pay и другие критерии.
Числовая доля 0.05 означает 5%. Изменение доли 0.04 -> 0.05 = +1 п.п., не +1%.
Учебный EL12 = Σ(EAD*PD12*LGD), EAD = текущий остаток; для DPD>=90 PD=1.
Это не IFRS9 ECL: нет стадий, дисконтирования, lifetime PD и сценарных весов.
В разложении Δ(w*r) = avg(w)*Δr + avg(r)*Δw; это атрибуция, не причинность.
Пустой знаменатель дает null, а не 0%. Когорты фиксированы, без новых выдач/списаний.
LLM не выдает индивидуальные кредитные решения. Любой вывод проверяет человек.
"""


def list_tools():
    return [{"name": name, "description": desc, "inputSchema": model.model_json_schema(),
             "annotations": {"readOnlyHint": True, "destructiveHint": False,
                             "idempotentHint": True, "openWorldHint": False}}
            for name, (model, _, desc) in TOOLS.items()]


def execute_tool(analytics: Analytics, name: str, arguments: dict):
    if name not in TOOLS:
        raise KeyError("Неизвестный инструмент")
    model, method, _ = TOOLS[name]
    validated = model.model_validate(arguments)
    return getattr(analytics, method)(**validated.model_dump())
