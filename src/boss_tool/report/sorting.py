"""P7 多级排序规则。

7 级优先级排序：
1. candidate_age_fit: ELIGIBLE → REVIEW → UNKNOWN → INELIGIBLE
2. within_3km: True → None → False
3. recommend_level: A → B → C → D
4. score: 高 → 低
5. distance_meter: 近 → 远，None 最后
6. recruiter_active_level: 3日内 → 本周 → 其他 → 未知
7. collected_at: 新 → 旧

设计原则：
- 纯确定性函数
- None 值安全降级
- 同分稳定排序（Python sort 稳定，保持原顺序）
"""

from __future__ import annotations

from boss_tool.report.models import ReportJob

# ==================== 排序优先级映射 ====================
# 数值越小优先级越高
_AGE_FIT_PRIORITY: dict[str, int] = {
    "eligible": 0,
    "review": 1,
    "unknown": 2,
    "ineligible": 3,
}

_RECOMMEND_LEVEL_PRIORITY: dict[str, int] = {
    "A": 0,
    "B": 1,
    "C": 2,
    "D": 3,
}

_RECRUITER_ACTIVE_PRIORITY: dict[str, int] = {
    "active_3d": 0,
    "active_this_week": 1,
    "active_long_ago": 2,
    "inactive": 3,
    "unknown": 4,
}


def _age_fit_key(job: ReportJob) -> int:
    """年龄适配优先级键。未知值降级为最低优先级（INELIGIBLE 之后）。"""
    fit = job.candidate_age_fit
    fit_str = fit.value if hasattr(fit, "value") else str(fit)
    return _AGE_FIT_PRIORITY.get(fit_str, 4)


def _within_3km_key(job: ReportJob) -> int:
    """3km 内优先级键：True → 0，None → 1，False → 2。"""
    if job.within_3km is None:
        return 1
    return 0 if job.within_3km else 2


def _recommend_level_key(job: ReportJob) -> int:
    """推荐等级优先级键。None 或未知值降级为最低优先级。"""
    level = job.recommend_level
    if level is None:
        return 4
    return _RECOMMEND_LEVEL_PRIORITY.get(level, 4)


def _score_key(job: ReportJob) -> int:
    """总分优先级键：高 → 低（取负数使大值在前）。None 视为 0。"""
    return -(job.score if job.score is not None else 0)


def _distance_key(job: ReportJob) -> float:
    """距离优先级键：近 → 远，None 最后。

    使用大数表示 None，确保 None 排在最后。
    """
    if job.distance_meter is None:
        return float("inf")
    return job.distance_meter


def _recruiter_active_key(job: ReportJob) -> int:
    """招聘者活跃优先级键。None 或未知值降级为最低优先级。"""
    level = job.recruiter_active_level
    if level is None:
        return 5
    return _RECRUITER_ACTIVE_PRIORITY.get(level, 5)


def _collected_at_key(job: ReportJob) -> float:
    """采集时间优先级键：新 → 旧（取负时间戳）。None 视为最早。"""
    if job.collected_at is None:
        return float("inf")
    return -job.collected_at.timestamp()


def _sort_key(job: ReportJob) -> tuple[int, int, int, int, float, int, float]:
    """组合排序键（7 级优先级）。"""
    return (
        _age_fit_key(job),
        _within_3km_key(job),
        _recommend_level_key(job),
        _score_key(job),
        _distance_key(job),
        _recruiter_active_key(job),
        _collected_at_key(job),
    )


def sort_jobs(jobs: list[ReportJob]) -> list[ReportJob]:
    """按 7 级优先级排序岗位。

    排序规则：
    1. candidate_age_fit: ELIGIBLE → REVIEW → UNKNOWN → INELIGIBLE
    2. within_3km: True → None → False
    3. recommend_level: A → B → C → D
    4. score: 高 → 低
    5. distance_meter: 近 → 远，None 最后
    6. recruiter_active_level: 3日内 → 本周 → 其他 → 未知
    7. collected_at: 新 → 旧

    Python sort 稳定，同分保持原顺序。

    Args:
        jobs: 待排序的岗位列表

    Returns:
        排序后的新列表（不修改原列表）
    """
    return sorted(jobs, key=_sort_key)


__all__ = ["sort_jobs"]
