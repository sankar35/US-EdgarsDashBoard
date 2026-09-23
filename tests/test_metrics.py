"""Deterministic unit tests for app.metrics with synthetic facts."""

import datetime as dt

from app.metrics import FactPoint, build_facts_index, compute_metrics


def _qtrs(concept, values, start_year=2024):
    """Build FactPoints for consecutive quarters CY{start_year}Q1.."""
    pts = []
    q = 0
    for i, v in enumerate(values):
        year = start_year + (i // 4)
        quarter = (i % 4) + 1
        end_month = quarter * 3
        end_day = 30 if end_month in (6, 9) else 31
        if end_month == 3:
            end_day = 31
        elif end_month == 12:
            end_day = 31
        pts.append(
            FactPoint(
                frame=f"CY{year}Q{quarter}",
                period_end=dt.date(year, end_month, end_day),
                value=v,
                form="10-Q",
            )
        )
    return pts


def _instant(concept, values, dates):
    return [
        FactPoint(frame=f"CY{d.year}Q{((d.month - 1) // 3) + 1}I", period_end=d, value=v, form="10-Q")
        for d, v in zip(dates, values)
    ]


def _facts(**kwargs):
    return kwargs


def test_ttm_sums_last_four_quarters():
    facts = _facts(revenues=_qtrs("revenues", [10, 10, 10, 10, 20, 20, 20, 20]))
    m = compute_metrics(facts)
    assert m["ttm_revenue"] == 80
    assert m["revenue_yoy"] == 1.0  # (80-40)/40


def test_fcf_handles_negative_capex():
    facts = _facts(
        ocf=_qtrs("ocf", [5] * 8),
        capex=_qtrs("capex", [-2] * 8),  # capex reported as negative outflow
    )
    m = compute_metrics(facts)
    assert m["ttm_ocf"] == 20
    assert m["ttm_fcf"] == 12  # 20 + (-8)


def test_margins_and_growth():
    facts = _facts(
        revenues=_qtrs("revenues", [100] * 4 + [120] * 4),
        gross_profit=_qtrs("gross_profit", [50] * 4 + [60] * 4),
        net_income=_qtrs("net_income", [10] * 4 + [12] * 4),
    )
    m = compute_metrics(facts)
    assert m["gross_margin"] == 0.5
    assert m["net_margin"] == 0.1
    assert m["revenue_yoy"] == 0.2


def test_balance_sheet_and_dilution():
    dates = [dt.date(2024, 12, 31), dt.date(2025, 12, 31)]
    facts = _facts(
        shares_outstanding=_instant("shares_outstanding", [100.0, 103.0], dates),
        long_term_debt=_instant("long_term_debt", [50.0, 40.0], dates),
        equity=_instant("equity", [200.0, 220.0], dates),
        cash=_instant("cash", [30.0, 35.0], dates),
    )
    m = compute_metrics(facts)
    assert m["shares_outstanding"] == 103.0
    # only 2 instant points -> no YoY (needs 5 quarterly points)
    assert m["shares_yoy"] is None
    assert abs(m["debt_to_equity"] - 40.0 / 220.0) < 1e-9
    assert m["net_debt"] == 5.0


def test_missing_concepts_never_crash():
    m = compute_metrics({})
    assert m["ttm_revenue"] is None
    assert m["gross_margin"] is None
    assert m["debt_to_equity"] is None
    # partial data still works
    m2 = compute_metrics(_facts(revenues=_qtrs("revenues", [10, 10])))
    assert m2["ttm_revenue"] is None  # fewer than 4 quarters
    assert m2["debt_to_equity"] is None


def test_build_facts_index_sorts():
    class Row:
        def __init__(self, concept, frame, period_end, value, form):
            self.concept, self.frame, self.period_end, self.value, self.form = (
                concept, frame, period_end, value, form,
            )

    rows = [
        Row("revenues", "CY2024Q2", dt.date(2024, 6, 30), 20.0, "10-Q"),
        Row("revenues", "CY2024Q1", dt.date(2024, 3, 31), 10.0, "10-Q"),
    ]
    idx = build_facts_index(rows)
    assert [p.value for p in idx["revenues"]] == [10.0, 20.0]
