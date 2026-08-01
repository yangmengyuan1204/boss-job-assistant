"""P7 报告四分区分类规则。

四个分区：
- 强烈推荐（绿色）: candidate_age_fit == ELIGIBLE AND within_3km == True AND recommend_level IN (A, B)
- 可考虑（蓝色）: ELIGIBLE 但距离/评分稍低；或 REVIEW 但其他条件较好
- 待人工确认（黄色）: UNKNOWN；或 REVIEW 且其他条件差；或关键信息缺失
- 不符合当前条件（红色）: INELIGIBLE；或明确 >3km；或其他明显不符合

不删除原则：
- 不得删除不符合岗位
- 报告需要让用户知道岗位为何未入选（展示未匹配规则 + 推荐解释）
- 所有岗位必须分入某个分区，不得遗漏
"""

from __future__ import annotations

from boss_tool.report import constants as C
from boss_tool.report.models import ReportJob, ReportSection, ReportSectionType, ReportSummary


def _get_age_fit_str(job: ReportJob) -> str:
    """获取年龄适配字符串值。"""
    fit = job.candidate_age_fit
    if hasattr(fit, "value"):
        return fit.value
    return str(fit)


def classify_section(job: ReportJob) -> ReportSectionType:
    """对单条岗位进行分区分类。

    分区优先级（从高到低判断，命中即返回）：
    1. 强烈推荐: ELIGIBLE AND within_3km==True AND level IN (A,B)
    2. 不符合: INELIGIBLE；或 within_3km==False（明确 >3km）
    3. 待人工确认: UNKNOWN；或 REVIEW 且（within_3km!=True OR level NOT IN A,B,C）
                       或描述缺失/招聘者未知
    4. 可考虑: 其他情况（ELIGIBLE 但距离/评分稍低；或 REVIEW 但条件较好）

    Args:
        job: 报告岗位

    Returns:
        ReportSectionType 枚举值
    """
    age_fit = _get_age_fit_str(job)
    level = job.recommend_level
    within_3km = job.within_3km

    # 1. 强烈推荐：ELIGIBLE + 3km 内 + A/B 等级
    if age_fit == "eligible" and within_3km is True and level in ("A", "B"):
        return ReportSectionType.STRONGLY_RECOMMEND

    # 2. 不符合：INELIGIBLE；或明确 >3km
    if age_fit == "ineligible":
        return ReportSectionType.NOT_MATCH
    if within_3km is False:
        return ReportSectionType.NOT_MATCH

    # 3. 待人工确认：UNKNOWN；或 REVIEW 且条件差；或关键信息缺失
    if age_fit == "unknown":
        return ReportSectionType.MANUAL_REVIEW

    if age_fit == "review":
        # REVIEW 且其他条件较好（3km 内 + A/B/C）→ 可考虑
        if within_3km is True and level in ("A", "B", "C"):
            return ReportSectionType.CONSIDER
        # REVIEW 且条件差 → 待人工确认
        return ReportSectionType.MANUAL_REVIEW

    # 4. 可考虑：ELIGIBLE 但距离/评分稍低
    # ELIGIBLE 但 within_3km 为 None 或 False（False 已在 2 中处理）
    # 或 ELIGIBLE 且 level 为 C/D
    if age_fit == "eligible":
        return ReportSectionType.CONSIDER

    # 兜底：未匹配任何条件，归为待人工确认
    return ReportSectionType.MANUAL_REVIEW


def build_sections(jobs: list[ReportJob]) -> list[ReportSection]:
    """将岗位列表分入四个分区。

    所有岗位必须分入某个分区，不得遗漏。
    分区顺序：强烈推荐 → 可考虑 → 待人工确认 → 不符合。

    Args:
        jobs: 已排序的岗位列表

    Returns:
        四个 ReportSection 列表（按固定顺序）
    """
    buckets: dict[ReportSectionType, list[ReportJob]] = {
        ReportSectionType.STRONGLY_RECOMMEND: [],
        ReportSectionType.CONSIDER: [],
        ReportSectionType.MANUAL_REVIEW: [],
        ReportSectionType.NOT_MATCH: [],
    }

    for job in jobs:
        section_type = classify_section(job)
        buckets[section_type].append(job)

    return [
        ReportSection(
            section_type=ReportSectionType.STRONGLY_RECOMMEND,
            title="强烈推荐",
            color=C.SECTION_COLOR_STRONGLY_RECOMMEND,
            jobs=buckets[ReportSectionType.STRONGLY_RECOMMEND],
        ),
        ReportSection(
            section_type=ReportSectionType.CONSIDER,
            title="可考虑",
            color=C.SECTION_COLOR_CONSIDER,
            jobs=buckets[ReportSectionType.CONSIDER],
        ),
        ReportSection(
            section_type=ReportSectionType.MANUAL_REVIEW,
            title="待人工确认",
            color=C.SECTION_COLOR_MANUAL_REVIEW,
            jobs=buckets[ReportSectionType.MANUAL_REVIEW],
        ),
        ReportSection(
            section_type=ReportSectionType.NOT_MATCH,
            title="不符合当前条件",
            color=C.SECTION_COLOR_NOT_MATCH,
            jobs=buckets[ReportSectionType.NOT_MATCH],
        ),
    ]


def build_summary(jobs: list[ReportJob]) -> ReportSummary:
    """根据岗位列表构造汇总统计。

    Args:
        jobs: 全部岗位列表

    Returns:
        ReportSummary 实例
    """

    summary = ReportSummary(total=len(jobs))

    for job in jobs:
        # 分区统计
        section = classify_section(job)
        if section == ReportSectionType.STRONGLY_RECOMMEND:
            summary.strongly_recommend_count += 1
        elif section == ReportSectionType.CONSIDER:
            summary.consider_count += 1
        elif section == ReportSectionType.MANUAL_REVIEW:
            summary.manual_review_count += 1
        elif section == ReportSectionType.NOT_MATCH:
            summary.not_match_count += 1

        # 年龄适配统计
        age_fit = _get_age_fit_str(job)
        if age_fit == "eligible":
            summary.eligible_count += 1
        elif age_fit == "review":
            summary.review_count += 1
        elif age_fit == "ineligible":
            summary.ineligible_count += 1
        else:
            summary.unknown_count += 1

        # 3km 内统计
        if job.within_3km is True:
            summary.within_3km_count += 1

        # 数据来源统计
        if job.data_source == "detail":
            summary.detail_source_count += 1
        else:
            summary.list_only_source_count += 1

    return summary


__all__ = [
    "classify_section",
    "build_sections",
    "build_summary",
]
