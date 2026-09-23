"""Transparent 0-100 investment-research score.

Rules are plain data (the ``RULES`` list) so the UI can render each rule,
its weight, and what the company earned. ``score_company`` applies them to
a metrics dict from :mod:`app.metrics` and returns the total plus a
per-rule breakdown. Missing inputs earn 0 for that rule (noted as n/a).
"""

from __future__ import annotations

from typing import Callable

# Each rule: key, display name, max points, plain-English description,
# and a function metrics -> (earned_points, detail_string).
RULES: list[dict] = []


def _rule(key: str, name: str, weight: int, description: str):
    def deco(fn: Callable[[dict], tuple[int, str]]):
        RULES.append(
            {
                "key": key,
                "name": name,
                "weight": weight,
                "description": description,
                "apply": fn,
            }
        )
        return fn

    return deco


def _band(value: float | None, bands: list[tuple[float, int]], detail: str) -> tuple[int, str]:
    """Award points by threshold bands [(min_value, points), ...] descending."""
    if value is None:
        return 0, f"n/a → 0 pts ({detail})"
    for threshold, pts in bands:
        if value > threshold:
            return pts, f"{detail}: {value:.1%} → {pts} pts"
    return 0, f"{detail}: {value:.1%} → 0 pts"


@_rule(
    "revenue_growth",
    "Revenue YoY growth",
    25,
    "TTM revenue vs the prior TTM: >20% earns 25, 10–20% earns 18, 0–10% earns 10, negative earns 0.",
)
def _revenue_growth(m: dict) -> tuple[int, str]:
    return _band(m.get("revenue_yoy"), [(0.20, 25), (0.10, 18), (0.0, 10)], "revenue YoY")


@_rule(
    "gross_margin",
    "Gross margin level",
    15,
    "TTM gross margin: >40% earns 15, 20–40% earns 10, below 20% earns 5.",
)
def _gross_margin(m: dict) -> tuple[int, str]:
    v = m.get("gross_margin")
    if v is None:
        return 0, "n/a → 0 pts (gross margin)"
    if v > 0.40:
        return 15, f"gross margin: {v:.1%} → 15 pts"
    if v > 0.20:
        return 10, f"gross margin: {v:.1%} → 10 pts"
    return 5, f"gross margin: {v:.1%} → 5 pts"


@_rule(
    "fcf",
    "Free cash flow",
    20,
    "TTM free cash flow: positive and growing YoY earns 20, positive earns 12, otherwise 0.",
)
def _fcf(m: dict) -> tuple[int, str]:
    fcf = m.get("ttm_fcf")
    if fcf is None:
        return 0, "n/a → 0 pts (FCF)"
    if fcf > 0 and (m.get("fcf_yoy") or 0) > 0:
        return 20, f"FCF positive and growing → 20 pts"
    if fcf > 0:
        return 12, f"FCF positive → 12 pts"
    return 0, f"FCF not positive → 0 pts"


@_rule(
    "leverage",
    "Debt / equity",
    15,
    "Balance-sheet leverage: <0.5 earns 15, 0.5–1.5 earns 8, above 1.5 earns 0.",
)
def _leverage(m: dict) -> tuple[int, str]:
    v = m.get("debt_to_equity")
    if v is None:
        return 0, "n/a → 0 pts (debt/equity)"
    if v < 0.5:
        return 15, f"debt/equity {v:.2f} → 15 pts"
    if v <= 1.5:
        return 8, f"debt/equity {v:.2f} → 8 pts"
    return 0, f"debt/equity {v:.2f} → 0 pts"


@_rule(
    "dilution",
    "Share dilution",
    10,
    "YoY change in shares outstanding: <2% earns 10, 2–5% earns 5, above 5% earns 0.",
)
def _dilution(m: dict) -> tuple[int, str]:
    v = m.get("shares_yoy")
    if v is None:
        return 0, "n/a → 0 pts (share growth)"
    if v < 0.02:
        return 10, f"share growth: {v:.1%} → 10 pts"
    if v <= 0.05:
        return 5, f"share growth: {v:.1%} → 5 pts"
    return 0, f"share growth: {v:.1%} → 0 pts"


@_rule(
    "earnings_quality",
    "Earnings quality",
    15,
    "Cash vs accruals: TTM operating cash flow above TTM net income earns 15, otherwise 5.",
)
def _earnings_quality(m: dict) -> tuple[int, str]:
    ocf, ni = m.get("ttm_ocf"), m.get("ttm_net_income")
    if ocf is None or ni is None:
        return 0, "n/a → 0 pts (OCF vs net income)"
    if ocf > ni:
        return 15, "OCF > net income → 15 pts"
    return 5, "OCF ≤ net income → 5 pts"


def score_company(metrics: dict) -> dict:
    """Apply RULES to a metrics dict.

    Returns {"total": int, "max": 100, "breakdown": [{key, name, weight,
    earned, detail}, ...]}.
    """
    breakdown = []
    total = 0
    for rule in RULES:
        try:
            earned, detail = rule["apply"](metrics or {})
        except Exception as exc:  # never let one rule break scoring
            earned, detail = 0, f"rule error → 0 pts ({exc})"
        earned = max(0, min(rule["weight"], int(earned)))
        total += earned
        breakdown.append(
            {
                "key": rule["key"],
                "name": rule["name"],
                "weight": rule["weight"],
                "earned": earned,
                "detail": detail,
                "description": rule["description"],
            }
        )
    return {"total": total, "max": sum(r["weight"] for r in RULES), "breakdown": breakdown}
