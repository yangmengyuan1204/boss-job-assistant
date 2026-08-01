"""P7 60 岁候选人适配纯函数。

依据 P6 的 age_status 计算候选年龄适配状态：
- ELIGIBLE: 适合 60 岁候选人
- REVIEW: 边界条件，建议联系招聘者确认
- INELIGIBLE: 不适合 60 岁候选人
- UNKNOWN: 未提取到年龄要求

设计原则：
- 纯确定性函数：相同输入永远产生相同输出
- 不接入 LLM / 机器学习
- 仅基于 P6 提取的 age_status，不重新解析文本
- 原因文本为固定文本，不拼接用户输入

边界说明：
- 「60岁以下」可能不包含刚好 60 岁；「60周岁以内」可能包含边界
- 仅靠文本无法保证，故 LIMIT_60 归为 REVIEW
- 不得把 UNKNOWN 当成适合，不得把"未写年龄"直接当成年龄不限
"""

from __future__ import annotations

from enum import Enum

from boss_tool.rules.models import AgeStatus


class CandidateAgeFit(str, Enum):
    """60 岁候选人适配状态。

    - ELIGIBLE: 适合 60 岁候选人
    - REVIEW: 边界条件，建议联系招聘者确认
    - INELIGIBLE: 不适合 60 岁候选人
    - UNKNOWN: 未提取到年龄要求
    """

    ELIGIBLE = "eligible"
    REVIEW = "review"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


# ==================== age_status -> (fit, reason) 映射表 ====================
# 依据《P7 需求冻结与技术设计初稿 v1.0》2.2 节适配规则表
# 原因文本全部为固定文本，不拼接任何动态内容
_AGE_FIT_MAP: dict[str, tuple[CandidateAgeFit, str]] = {
    AgeStatus.NO_LIMIT.value: (
        CandidateAgeFit.ELIGIBLE,
        "年龄不限，适合 60 岁候选人",
    ),
    AgeStatus.LIMIT_65.value: (
        CandidateAgeFit.ELIGIBLE,
        "65岁以下，适合 60 岁候选人",
    ),
    AgeStatus.LIMIT_60.value: (
        CandidateAgeFit.REVIEW,
        "边界条件，建议联系招聘者确认",
    ),
    AgeStatus.LIMIT_55.value: (
        CandidateAgeFit.INELIGIBLE,
        "年龄上限 55 岁，不适合 60 岁候选人",
    ),
    AgeStatus.LIMIT_50.value: (
        CandidateAgeFit.INELIGIBLE,
        "年龄上限 50 岁，不适合 60 岁候选人",
    ),
    AgeStatus.LIMIT_45.value: (
        CandidateAgeFit.INELIGIBLE,
        "年龄上限 45 岁，不适合 60 岁候选人",
    ),
    AgeStatus.OTHER.value: (
        CandidateAgeFit.REVIEW,
        "年龄表述模糊，建议联系招聘者确认",
    ),
    AgeStatus.UNKNOWN.value: (
        CandidateAgeFit.UNKNOWN,
        "未提取到年龄要求，建议联系招聘者确认",
    ),
}


def compute_age_fit(age_status: AgeStatus | str) -> tuple[CandidateAgeFit, str]:
    """根据 age_status 计算 60 岁适配状态与固定原因文本。

    纯确定性函数：相同输入永远产生相同输出。
    不接入 LLM，不解析文本，不拼接动态内容。

    Args:
        age_status: AgeStatus 枚举值或字符串值（如 "limit_60"、"unknown"）

    Returns:
        (candidate_age_fit, candidate_age_fit_reason)
        - candidate_age_fit: CandidateAgeFit 枚举值
        - candidate_age_fit_reason: 固定原因文本

    Examples:
        >>> compute_age_fit(AgeStatus.NO_LIMIT)
        (<CandidateAgeFit.ELIGIBLE: 'eligible'>, '年龄不限，适合 60 岁候选人')
        >>> compute_age_fit("limit_55")
        (<CandidateAgeFit.INELIGIBLE: 'ineligible'>, '年龄上限 55 岁，不适合 60 岁候选人')
        >>> compute_age_fit("unknown")
        (<CandidateAgeFit.UNKNOWN: 'unknown'>, '未提取到年龄要求，建议联系招聘者确认')
    """
    # 统一转换为字符串值
    status_str = age_status.value if hasattr(age_status, "value") else str(age_status)

    # 未知值安全降级为 UNKNOWN（防御性）
    if status_str not in _AGE_FIT_MAP:
        return (
            CandidateAgeFit.UNKNOWN,
            "未提取到年龄要求，建议联系招聘者确认",
        )

    return _AGE_FIT_MAP[status_str]


__all__ = [
    "CandidateAgeFit",
    "compute_age_fit",
]
