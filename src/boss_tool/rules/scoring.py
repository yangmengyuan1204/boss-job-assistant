"""P6 评分与推荐等级计算。

纯函数，无副作用，便于测试。
所有分数与阈值来自 constants，不硬编码。
"""

from __future__ import annotations

from boss_tool.rules.constants import (
    MAX_SCORE,
    RECOMMEND_LEVEL_A_THRESHOLD,
    RECOMMEND_LEVEL_B_THRESHOLD,
    RECOMMEND_LEVEL_C_THRESHOLD,
    SALARY_THRESHOLD_HIGH,
    SALARY_THRESHOLD_LOW,
    SCORE_DISTANCE_3_TO_5KM,
    SCORE_DISTANCE_OVER_5KM,
    SCORE_DISTANCE_WITHIN_3KM,
    SCORE_RECRUITER_ACTIVE_3D,
    SCORE_RECRUITER_ACTIVE_7D,
    SCORE_RECRUITER_ACTIVE_OTHER,
    SCORE_SALARY_HIGH,
    SCORE_SALARY_LOW,
    SCORE_SALARY_OTHER,
)
from boss_tool.rules.models import RecommendLevel


def score_distance(distance_meter: float | None) -> int:
    """距离评分。

    - <=3000 -> +30
    - 3000< <=5000 -> +10
    - >5000 -> 0
    - None -> 0

    Args:
        distance_meter: 距离（米），None 表示无数据

    Returns:
        分数
    """
    if distance_meter is None:
        return 0
    if distance_meter <= 3000.0:
        return SCORE_DISTANCE_WITHIN_3KM
    if distance_meter <= 5000.0:
        return SCORE_DISTANCE_3_TO_5KM
    return SCORE_DISTANCE_OVER_5KM


def score_recruiter_active(level: str) -> int:
    """招聘者活跃评分。

    - active_3d -> +20
    - active_this_week -> +10
    - 其他 -> 0

    Args:
        level: ActivityCategory 枚举值（字符串）

    Returns:
        分数
    """
    if level == "active_3d":
        return SCORE_RECRUITER_ACTIVE_3D
    if level == "active_this_week":
        return SCORE_RECRUITER_ACTIVE_7D
    return SCORE_RECRUITER_ACTIVE_OTHER


def score_salary(salary_min_monthly: int | None) -> int:
    """薪资评分。

    基于月薪最低值（已解析为整数）：
    - >=4000 -> +10
    - >=3000 -> +5
    - 其他 -> 0

    Args:
        salary_min_monthly: 月薪最低值（元），None 表示无法解析

    Returns:
        分数
    """
    if salary_min_monthly is None:
        return SCORE_SALARY_OTHER
    if salary_min_monthly >= SALARY_THRESHOLD_HIGH:
        return SCORE_SALARY_HIGH
    if salary_min_monthly >= SALARY_THRESHOLD_LOW:
        return SCORE_SALARY_LOW
    return SCORE_SALARY_OTHER


def total_score(*parts: int) -> int:
    """汇总各部分分数，上限 100。

    Args:
        parts: 各部分分数

    Returns:
        总分（0..100）
    """
    return min(sum(parts), MAX_SCORE)


def recommend_level(score: int) -> RecommendLevel:
    """根据总分计算推荐等级。

    - >=85 -> A
    - 70~84 -> B
    - 50~69 -> C
    - <50 -> D

    Args:
        score: 总分

    Returns:
        RecommendLevel
    """
    if score >= RECOMMEND_LEVEL_A_THRESHOLD:
        return RecommendLevel.A
    if score >= RECOMMEND_LEVEL_B_THRESHOLD:
        return RecommendLevel.B
    if score >= RECOMMEND_LEVEL_C_THRESHOLD:
        return RecommendLevel.C
    return RecommendLevel.D


__all__ = [
    "score_distance",
    "score_recruiter_active",
    "score_salary",
    "total_score",
    "recommend_level",
]
