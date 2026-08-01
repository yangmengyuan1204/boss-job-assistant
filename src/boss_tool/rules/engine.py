"""P6 规则引擎入口。

RuleEngine 负责：
- 岗位分类（classify_category）
- 年龄文本提取（extract_age）
- 招聘者活跃解析（parse_recruiter_active）
- 劳动强度检测（detect_labor_intensity）
- 薪资解析（parse_salary_min）
- 综合评估（evaluate）

约束：
- 不接入 LLM / 机器学习
- 每条 explanation 为固定文本
- 不破坏 GeoRepository / JobRepository / JobListRepository / JobDetailRepository
- 纯确定性：相同输入永远产生相同输出
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from boss_tool.enums import ActivityCategory
from boss_tool.rules import constants as C
from boss_tool.rules.models import AgeStatus, RuleResult
from boss_tool.rules.scoring import (
    recommend_level,
    score_distance,
    score_recruiter_active,
    score_salary,
    total_score,
)


# ==================== 输入数据 ====================
@dataclass(frozen=True)
class JobInput:
    """规则引擎输入数据（从 job_detail / job_list 聚合）。

    所有字段允许为空，规则引擎对空值安全降级。
    不直接依赖 JobDetailRecord / JobListRecord，保持解耦。
    """

    job_id: str
    title: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    salary_text: str | None = None
    recruiter_active_text: str | None = None
    distance_meter: float | None = None


# ==================== 岗位分类 ====================
def classify_category(
    title: str | None,
    tags: tuple[str, ...] | list[str] | None,
    description: str | None,
) -> str:
    """岗位分类。

    基于标题、标签、描述共同判断。
    按优先级顺序匹配（保洁 > 保安 > 门卫 > 宿管 > 绿化 > 环卫 > 其他）。
    同一岗位命中多个类别时取首个（优先级高者）。

    Args:
        title: 岗位标题
        tags: 标签列表
        description: 岗位描述

    Returns:
        类别字符串（保洁/保安/门卫/宿管/绿化/环卫/其他）
    """
    text = _concat_text(title, tags, description)
    if not text:
        return C.JOB_CATEGORY_OTHER

    for category, keywords in C.JOB_CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return category
    return C.JOB_CATEGORY_OTHER


# ==================== 年龄提取 ====================
# 匹配 "N岁以下" / "N周岁以下"
_AGE_LIMIT_RE = re.compile(r"(\d{1,3})\s*周?岁?以下")


def extract_age(
    title: str | None,
    tags: tuple[str, ...] | list[str] | None,
    description: str | None,
) -> tuple[str | None, AgeStatus]:
    """提取年龄要求原文与状态。

    只提取，不推断是否可干。
    优先级：
    1. 命中"年龄不限/无年龄要求" -> NO_LIMIT
    2. 命中"N岁以下" 且 N 在 {45,50,55,60,65} -> 对应 LIMIT_*
    3. 命中其他年龄表述 -> OTHER
    4. 无任何年龄文本 -> UNKNOWN

    Args:
        title: 岗位标题
        tags: 标签列表
        description: 岗位描述

    Returns:
        (age_requirement_text, age_status)
        - age_requirement_text: 命中的原文片段（首个匹配），无则 None
        - age_status: AgeStatus 枚举值
    """
    text = _concat_text(title, tags, description)
    if not text:
        return None, AgeStatus.UNKNOWN

    # 1. 无年龄限制
    for kw in C.AGE_NO_LIMIT_KEYWORDS:
        if kw in text:
            return kw, AgeStatus.NO_LIMIT

    # 2. N岁以下档位
    for status, nums in C.AGE_LIMIT_PATTERNS.items():
        for n in nums:
            pattern = rf"{n}\s*周?岁?以下"
            m = re.search(pattern, text)
            if m:
                return m.group(0).strip(), AgeStatus(status)

    # 3. 其他年龄表述（如"18-50岁"、"40岁以下优先"）
    #    匹配任意 "N岁以下" 但 N 不在档位内，或 "N-M岁" 区间
    m = _AGE_LIMIT_RE.search(text)
    if m:
        return m.group(0).strip(), AgeStatus.OTHER

    # 匹配 "N-M岁" 区间
    range_m = re.search(r"\d{1,3}\s*[-~]\s*\d{1,3}\s*周?岁", text)
    if range_m:
        return range_m.group(0).strip(), AgeStatus.OTHER

    # 4. 无年龄文本
    return None, AgeStatus.UNKNOWN


# ==================== 招聘者活跃解析 ====================
def parse_recruiter_active(recruiter_active_text: str | None) -> ActivityCategory:
    """解析招聘者活跃文本为等级。

    - 3日内（今日/昨日/3日内/刚刚活跃）-> ACTIVE_3D
    - 7日内（本周/7日内/近一周）-> ACTIVE_THIS_WEEK
    - 其他文本 -> ACTIVE_LONG_AGO
    - None/空 -> UNKNOWN

    Args:
        recruiter_active_text: 招聘者活跃度原文

    Returns:
        ActivityCategory 枚举值
    """
    if not recruiter_active_text or not recruiter_active_text.strip():
        return ActivityCategory.UNKNOWN

    text = recruiter_active_text.strip()

    for kw in C.RECRUITER_ACTIVE_3D_KEYWORDS:
        if kw in text:
            return ActivityCategory.ACTIVE_3D

    for kw in C.RECRUITER_ACTIVE_7D_KEYWORDS:
        if kw in text:
            return ActivityCategory.ACTIVE_THIS_WEEK

    # 有文本但不匹配上述关键词
    return ActivityCategory.ACTIVE_LONG_AGO


# ==================== 劳动强度检测 ====================
def detect_labor_intensity(
    title: str | None,
    tags: tuple[str, ...] | list[str] | None,
    description: str | None,
) -> list[str]:
    """检测劳动强度关键字。

    命中任一即返回该关键字（不删除岗位，仅增加 warning）。
    返回命中的关键字列表（去重，保持首次出现顺序）。

    Args:
        title: 岗位标题
        tags: 标签列表
        description: 岗位描述

    Returns:
        命中的关键字列表（可能为空）
    """
    text = _concat_text(title, tags, description)
    if not text:
        return []

    seen: set[str] = set()
    matched: list[str] = []
    for kw in C.LABOR_INTENSITY_KEYWORDS:
        if kw in text and kw not in seen:
            seen.add(kw)
            matched.append(kw)
    return matched


# ==================== 薪资解析 ====================
# 匹配 "4000-6000" / "4k-6k" / "4000元/月" 中的首个数字
# 允许 1-6 位数字以支持 "4k"（4*1000=4000）格式
_SALARY_NUM_RE = re.compile(r"(\d{1,6})\s*[kK千万]?")


def parse_salary_min(salary_text: str | None) -> int | None:
    """解析薪资文本为月薪最低值（整数元）。

    保守策略：取薪资区间中出现的首个数字作为最低值。
    - "4000-6000" -> 4000
    - "4k-6k" -> 4000（k -> *1000）
    - "4000元/月" -> 4000
    - "面议" / None / 无法解析 -> None

    Args:
        salary_text: 薪资原文

    Returns:
        月薪最低值（元），无法解析返回 None
    """
    if not salary_text or not salary_text.strip():
        return None

    text = salary_text.strip()

    # 面议/薪资面议
    if "面议" in text:
        return None

    m = _SALARY_NUM_RE.search(text)
    if not m:
        return None

    num_str = m.group(1)
    try:
        num = int(num_str)
    except ValueError:
        return None

    # k/K 后缀 -> *1000
    # 检查数字后是否紧跟 k/K
    rest = text[m.end(1) :]
    if rest and rest[0] in ("k", "K"):
        num = num * 1000

    # 合理性校验：月薪应在 0..1000000
    if num < 0 or num > 1_000_000:
        return None
    return num


# ==================== 工具函数 ====================
def _concat_text(
    title: str | None,
    tags: tuple[str, ...] | list[str] | None,
    description: str | None,
) -> str:
    """拼接标题、标签、描述为一个文本（用于关键字匹配）。

    用空格分隔，避免跨字段误匹配。
    """
    parts: list[str] = []
    if title:
        parts.append(title)
    if tags:
        parts.extend(t for t in tags if t)
    if description:
        parts.append(description)
    return " ".join(parts)


# ==================== RuleEngine ====================
class RuleEngine:
    """岗位筛选规则引擎。

    纯确定性规则，不接入 LLM / 机器学习。
    每条 explanation 为固定文本。

    用法：
        engine = RuleEngine()
        result = engine.evaluate(JobInput(...))
    """

    def evaluate(self, job: JobInput) -> RuleResult:
        """综合评估岗位，返回 RuleResult。

        流程：
        1. 岗位分类
        2. 年龄提取
        3. 招聘者活跃解析
        4. 劳动强度检测
        5. 薪资解析
        6. 距离评分
        7. 汇总总分 + 推荐等级
        8. 生成固定解释文本

        Args:
            job: 岗位输入数据

        Returns:
            RuleResult
        """
        matched_rules: list[str] = []
        failed_rules: list[str] = []
        explanations: list[str] = []
        warnings: list[str] = []
        score_breakdown: dict[str, int] = {}

        # 1. 岗位分类
        category = classify_category(job.title, job.tags, job.description)
        cat_score = C.JOB_CATEGORY_SCORES.get(category, 0)
        score_breakdown["category"] = cat_score
        if cat_score > 0:
            matched_rules.append(f"category:{category}")
            explanations.append(
                f"{C.EXPLAIN_CATEGORY_PREFIX}{category}{C.EXPLAIN_CATEGORY_SCORED.format(score=cat_score)}"
            )
        else:
            failed_rules.append("category:scored")
            explanations.append(C.EXPLAIN_CATEGORY_OTHER)

        # 2. 年龄提取
        age_text, age_status = extract_age(job.title, job.tags, job.description)
        if age_text is not None:
            matched_rules.append(
                f"age:{age_status.value if hasattr(age_status, 'value') else age_status}"
            )
            explanations.append(
                C.EXPLAIN_AGE_EXTRACTED.format(text=age_text, status=age_status.value)
            )
        else:
            failed_rules.append("age:extracted")
            explanations.append(C.EXPLAIN_AGE_NONE)

        # 3. 招聘者活跃
        active_level = parse_recruiter_active(job.recruiter_active_text)
        rec_score = score_recruiter_active(active_level.value)
        score_breakdown["recruiter"] = rec_score
        if rec_score > 0:
            matched_rules.append(f"recruiter:{active_level.value}")
        else:
            failed_rules.append("recruiter:scored")

        if active_level == ActivityCategory.ACTIVE_3D:
            explanations.append(C.EXPLAIN_RECRUITER_3D)
        elif active_level == ActivityCategory.ACTIVE_THIS_WEEK:
            explanations.append(C.EXPLAIN_RECRUITER_7D)
        else:
            explanations.append(C.EXPLAIN_RECRUITER_OTHER)

        # 4. 劳动强度检测
        labor_tags = detect_labor_intensity(job.title, job.tags, job.description)
        if labor_tags:
            matched_rules.append("labor_intensity:detected")
            warning_text = C.EXPLAIN_LABOR_WARNING_PREFIX + "、".join(labor_tags)
            warnings.append(warning_text)
            explanations.append(warning_text)
        else:
            failed_rules.append("labor_intensity:detected")

        # 5. 薪资
        salary_min = parse_salary_min(job.salary_text)
        sal_score = score_salary(salary_min)
        score_breakdown["salary"] = sal_score
        if sal_score > 0:
            matched_rules.append(f"salary:{sal_score}")
        else:
            failed_rules.append("salary:scored")

        if sal_score == C.SCORE_SALARY_HIGH:
            explanations.append(C.EXPLAIN_SALARY_HIGH)
        elif sal_score == C.SCORE_SALARY_LOW:
            explanations.append(C.EXPLAIN_SALARY_LOW)
        else:
            explanations.append(C.EXPLAIN_SALARY_OTHER)

        # 6. 距离
        dist_score = score_distance(job.distance_meter)
        score_breakdown["distance"] = dist_score
        if dist_score > 0:
            matched_rules.append(f"distance:{dist_score}")
        else:
            failed_rules.append("distance:scored")

        if job.distance_meter is None:
            explanations.append(C.EXPLAIN_DISTANCE_NONE)
        elif job.distance_meter <= C.DISTANCE_3KM_M:
            explanations.append(C.EXPLAIN_DISTANCE_WITHIN_3KM)
        elif job.distance_meter <= C.DISTANCE_5KM_M:
            explanations.append(C.EXPLAIN_DISTANCE_3_TO_5KM)
        else:
            explanations.append(C.EXPLAIN_DISTANCE_OVER_5KM)

        # 7. 汇总
        final_score = total_score(cat_score, rec_score, sal_score, dist_score)
        level = recommend_level(final_score)

        return RuleResult(
            score=final_score,
            recommend_level=level,
            job_category=category,
            age_requirement_text=age_text,
            age_status=age_status,
            recruiter_active_level=active_level,
            distance_meter=job.distance_meter,
            matched_rules=matched_rules,
            failed_rules=failed_rules,
            warnings=warnings,
            explanations=explanations,
            labor_intensity_tags=labor_tags,
            score_breakdown=score_breakdown,
        )


__all__ = [
    "JobInput",
    "RuleEngine",
    "classify_category",
    "extract_age",
    "parse_recruiter_active",
    "detect_labor_intensity",
    "parse_salary_min",
]
