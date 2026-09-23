"""Deterministic unit tests for app.scoring."""

from app.scoring import RULES, score_company


def _metrics(**overrides):
    base = {
        "revenue_yoy": 0.25,
        "gross_margin": 0.45,
        "ttm_fcf": 100.0,
        "fcf_yoy": 0.10,
        "debt_to_equity": 0.3,
        "shares_yoy": 0.01,
        "ttm_ocf": 120.0,
        "ttm_net_income": 100.0,
    }
    base.update(overrides)
    return base


def test_rules_cover_100_points():
    assert sum(r["weight"] for r in RULES) == 100
    assert len(RULES) == 6
    for r in RULES:
        assert {"key", "name", "weight", "description", "apply"} <= set(r)


def test_perfect_company_scores_100():
    s = score_company(_metrics())
    assert s["total"] == 100
    assert s["max"] == 100
    assert len(s["breakdown"]) == 6


def test_band_boundaries():
    assert score_company(_metrics(revenue_yoy=0.21))["breakdown"][0]["earned"] == 25
    assert score_company(_metrics(revenue_yoy=0.15))["breakdown"][0]["earned"] == 18
    assert score_company(_metrics(revenue_yoy=0.05))["breakdown"][0]["earned"] == 10
    assert score_company(_metrics(revenue_yoy=-0.05))["breakdown"][0]["earned"] == 0


def test_weak_company_scores_low():
    s = score_company(
        _metrics(
            revenue_yoy=-0.10,
            gross_margin=0.10,
            ttm_fcf=-50.0,
            debt_to_equity=2.0,
            shares_yoy=0.08,
            ttm_ocf=10.0,
            ttm_net_income=100.0,
        )
    )
    # growth 0 + margin 5 + fcf 0 + leverage 0 + dilution 0 + quality 5
    assert s["total"] == 10


def test_missing_data_never_crashes_and_scores_zero():
    s = score_company({})
    assert s["total"] == 0
    assert all(b["earned"] == 0 for b in s["breakdown"])
    s2 = score_company(None)
    assert s2["total"] == 0


def test_earnings_quality_and_fcf_nuances():
    # FCF positive but shrinking -> 12, not 20
    s = score_company(_metrics(ttm_fcf=100.0, fcf_yoy=-0.05))
    fcf_rule = next(b for b in s["breakdown"] if b["key"] == "fcf")
    assert fcf_rule["earned"] == 12
    # OCF below net income -> 5, not 15
    s = score_company(_metrics(ttm_ocf=50.0, ttm_net_income=100.0))
    q = next(b for b in s["breakdown"] if b["key"] == "earnings_quality")
    assert q["earned"] == 5
