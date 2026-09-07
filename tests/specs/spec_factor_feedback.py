"""Spec: Factor Feedback Classification Contract

Claims covering _classify_factor() quality gates:
- minimum sample size before preferred promotion
- outlier-driven PF detection
- hard_blocked status for consistently failing factors

Contract: factor_feedback
Claim prefix: FF
"""
from __future__ import annotations

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "FF-MIN-TRADES", "factor_feedback",
        "§classify_factor min_trades_for_preferred",
        "L2b",
        "preferred 状态需要 trade_count >= 8（原 3）；"
        "3 笔的 win_rate 置信区间横跨 0-1，不可信。"
        "win_rate >= 0.45 也是门槛条件之一。",
        status="implemented",
    ),
)
def test_factor_preferred_requires_8_trades():
    """7 笔不应晋升 preferred；8 笔且满足所有条件则可以。"""
    from aletheia.analytics.factor_feedback import _classify_factor

    # 7 trades: should NOT be preferred regardless of good stats
    status_7, _ = _classify_factor(7, 3, 0.5, 0.55, 2.0)
    assert status_7 != "preferred", (
        f"7 笔不应晋升 preferred（需要 >= 8），得到 {status_7!r}"
    )

    # 8 trades with good stats: should be preferred
    status_8, _ = _classify_factor(8, 3, 0.5, 0.55, 2.0)
    assert status_8 == "preferred", (
        f"8 笔 + expectancy>0 + pf>=1.2 + wr>=0.45 应晋升 preferred，得到 {status_8!r}"
    )


@spec_claim(
    SpecClaim(
        "FF-OUTLIER-GATE", "factor_feedback",
        "§classify_factor outlier_driven_pf_check",
        "L2b",
        "PF > 5.0 且 n < 15 → 返回 active 而非 preferred（单笔异常大盈利代理）。"
        "3 笔 +30% 赢 2 笔小亏 → PF 可能极高，但实际无统计意义。",
        status="implemented",
    ),
)
def test_factor_outlier_pf_not_preferred():
    """PF=6.5, n=10 应返回 active（outlier gate），reason 包含 outlier。"""
    from aletheia.analytics.factor_feedback import _classify_factor

    status, reason = _classify_factor(10, 3, 0.8, 0.60, 6.5)
    assert status == "active", (
        f"PF=6.5, n=10 应返回 active（outlier gate），得到 {status!r}"
    )
    assert "outlier" in reason, (
        f"reason 应包含 'outlier' 字样，得到: {reason!r}"
    )


@spec_claim(
    SpecClaim(
        "FF-HARD-BLOCKED", "factor_feedback",
        "§classify_factor hard_blocked status",
        "L2b",
        "trade_count >= 10 且 win_rate < 0.30 且 expectancy < -0.5 → 返回 hard_blocked 诊断。"
        "该诊断本身不再获得 ranker 权限；是否应用由前瞻实验授权字段决定。",
        status="implemented",
    ),
)
def test_factor_hard_blocked_status():
    """n=12, wr=0.25, exp=-0.8 应返回 hard_blocked。"""
    from aletheia.analytics.factor_feedback import _classify_factor

    status, reason = _classify_factor(12, 3, -0.8, 0.25, 0.3)
    assert status == "hard_blocked", (
        f"n=12, wr=0.25, exp=-0.8 应返回 hard_blocked，得到 {status!r}"
    )
