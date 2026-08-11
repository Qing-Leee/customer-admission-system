"""评级与风控系数映射模块。

根据评分总分映射到对应的信用评级和风控系数。
评级体系：AAA / AA / A / BBB / BB / B / UNQUALIFIED
"""

from typing import Tuple


# 评级阈值表（按总分从高到低匹配）
# (下限, 上限, 评级, 风控系数)
_RATING_THRESHOLDS = [
    (90, None, "AAA", 0.50),
    (80, 90, "AA", 0.70),
    (70, 80, "A", 0.85),
    (60, 70, "BBB", 1.00),
    (50, 60, "BB", 1.20),
    (40, 50, "B", 1.50),
    (0, 40, "UNQUALIFIED", 2.00),
]


def map_rating(total_score: float) -> Tuple[str, float]:
    """根据总分映射评级与风控系数。

    Args:
        total_score: 评分总分（0~100）。

    Returns:
        (rating, risk_coefficient) 元组。
        rating 取值：AAA / AA / A / BBB / BB / B / UNQUALIFIED
        risk_coefficient 为风控系数，越低表示风险越小。
    """
    score = float(total_score or 0)

    for lower, upper, rating, coefficient in _RATING_THRESHOLDS:
        if upper is None:
            if score >= lower:
                return rating, coefficient
        elif lower <= score < upper:
            return rating, coefficient

    # 兜底：分数为负数或其他异常情况
    return "UNQUALIFIED", 2.00
