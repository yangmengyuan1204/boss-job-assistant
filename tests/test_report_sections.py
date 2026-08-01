"""P7 报告四分区分类规则测试。

覆盖：
- 强烈推荐：ELIGIBLE + 3km内 + A/B 等级
- 可考虑：ELIGIBLE 但距离/评分稍低；或 REVIEW 但条件较好
- 待人工确认：UNKNOWN；或 REVIEW 且条件差
- 不符合：INELIGIBLE；或明确 >3km
- build_sections 四分区顺序
- build_summary 汇总统计
- 所有岗位必须分入某个分区
- 不删除不符合岗位
"""

from __future__ import annotations

from boss_tool.report.age_fit import CandidateAgeFit
from boss_tool.report.models import ReportJob, ReportSectionType
from boss_tool.report.sections import build_sections, build_summary, classify_section


def _make_job(
    job_id: str = "job-001",
    age_fit: CandidateAgeFit = CandidateAgeFit.UNKNOWN,
    within_3km: bool | None = None,
    recommend_level: str | None = None,
    score: int | None = None,
    data_source: str = "list_only",
) -> ReportJob:
    """构造测试用 ReportJob。"""
    return ReportJob(
        job_id=job_id,
        candidate_age_fit=age_fit,
        within_3km=within_3km,
        recommend_level=recommend_level,
        score=score,
        data_source=data_source,
    )


# ==================== classify_section ====================
class TestStronglyRecommend:
    """强烈推荐分区。"""

    def test_eligible_within3km_level_a(self) -> None:
        """ELIGIBLE + 3km内 + A级 → 强烈推荐。"""
        job = _make_job(
            age_fit=CandidateAgeFit.ELIGIBLE,
            within_3km=True,
            recommend_level="A",
        )
        assert classify_section(job) == ReportSectionType.STRONGLY_RECOMMEND

    def test_eligible_within3km_level_b(self) -> None:
        """ELIGIBLE + 3km内 + B级 → 强烈推荐。"""
        job = _make_job(
            age_fit=CandidateAgeFit.ELIGIBLE,
            within_3km=True,
            recommend_level="B",
        )
        assert classify_section(job) == ReportSectionType.STRONGLY_RECOMMEND

    def test_eligible_within3km_level_c_not_strong(self) -> None:
        """ELIGIBLE + 3km内 + C级 → 不是强烈推荐。"""
        job = _make_job(
            age_fit=CandidateAgeFit.ELIGIBLE,
            within_3km=True,
            recommend_level="C",
        )
        assert classify_section(job) != ReportSectionType.STRONGLY_RECOMMEND


class TestNotMatch:
    """不符合分区。"""

    def test_ineligible_is_not_match(self) -> None:
        """INELIGIBLE → 不符合。"""
        job = _make_job(age_fit=CandidateAgeFit.INELIGIBLE)
        assert classify_section(job) == ReportSectionType.NOT_MATCH

    def test_over_3km_is_not_match(self) -> None:
        """明确 >3km → 不符合。"""
        job = _make_job(
            age_fit=CandidateAgeFit.ELIGIBLE,
            within_3km=False,
        )
        assert classify_section(job) == ReportSectionType.NOT_MATCH


class TestManualReview:
    """待人工确认分区。"""

    def test_unknown_is_manual_review(self) -> None:
        """UNKNOWN → 待人工确认。"""
        job = _make_job(age_fit=CandidateAgeFit.UNKNOWN)
        assert classify_section(job) == ReportSectionType.MANUAL_REVIEW

    def test_review_with_poor_conditions(self) -> None:
        """REVIEW 且条件差 → 待人工确认。"""
        job = _make_job(
            age_fit=CandidateAgeFit.REVIEW,
            within_3km=None,
            recommend_level="D",
        )
        assert classify_section(job) == ReportSectionType.MANUAL_REVIEW

    def test_review_outside_3km_is_not_match_not_review(self) -> None:
        """REVIEW 但 >3km → 不符合（NOT_MATCH 优先）。"""
        job = _make_job(
            age_fit=CandidateAgeFit.REVIEW,
            within_3km=False,
        )
        assert classify_section(job) == ReportSectionType.NOT_MATCH


class TestConsider:
    """可考虑分区。"""

    def test_eligible_none_distance_level_c(self) -> None:
        """ELIGIBLE + 距离 None + C级 → 可考虑。"""
        job = _make_job(
            age_fit=CandidateAgeFit.ELIGIBLE,
            within_3km=None,
            recommend_level="C",
        )
        assert classify_section(job) == ReportSectionType.CONSIDER

    def test_review_with_good_conditions(self) -> None:
        """REVIEW + 3km内 + A/B/C级 → 可考虑。"""
        job = _make_job(
            age_fit=CandidateAgeFit.REVIEW,
            within_3km=True,
            recommend_level="B",
        )
        assert classify_section(job) == ReportSectionType.CONSIDER


# ==================== build_sections ====================
class TestBuildSections:
    """build_sections 四分区构建。"""

    def test_returns_four_sections(self) -> None:
        """返回四个分区。"""
        sections = build_sections([])
        assert len(sections) == 4

    def test_section_order(self) -> None:
        """分区顺序：强烈推荐 → 可考虑 → 待人工确认 → 不符合。"""
        sections = build_sections([])
        assert sections[0].section_type == ReportSectionType.STRONGLY_RECOMMEND
        assert sections[1].section_type == ReportSectionType.CONSIDER
        assert sections[2].section_type == ReportSectionType.MANUAL_REVIEW
        assert sections[3].section_type == ReportSectionType.NOT_MATCH

    def test_all_jobs_classified(self) -> None:
        """所有岗位都被分入某个分区。"""
        jobs = [
            _make_job(
                "strong", age_fit=CandidateAgeFit.ELIGIBLE, within_3km=True, recommend_level="A"
            ),
            _make_job(
                "consider", age_fit=CandidateAgeFit.ELIGIBLE, within_3km=None, recommend_level="C"
            ),
            _make_job("review", age_fit=CandidateAgeFit.UNKNOWN),
            _make_job("not_match", age_fit=CandidateAgeFit.INELIGIBLE),
        ]
        sections = build_sections(jobs)
        total = sum(s.count for s in sections)
        assert total == 4

    def test_does_not_delete_jobs(self) -> None:
        """不删除任何岗位（包括不符合的）。"""
        jobs = [
            _make_job("not_match_1", age_fit=CandidateAgeFit.INELIGIBLE),
            _make_job("not_match_2", within_3km=False, age_fit=CandidateAgeFit.ELIGIBLE),
            _make_job(
                "good", age_fit=CandidateAgeFit.ELIGIBLE, within_3km=True, recommend_level="A"
            ),
        ]
        sections = build_sections(jobs)
        total = sum(s.count for s in sections)
        assert total == 3

    def test_empty_jobs(self) -> None:
        """空列表返回四个空分区。"""
        sections = build_sections([])
        for section in sections:
            assert section.count == 0
            assert section.jobs == []

    def test_section_count_matches_jobs(self) -> None:
        """分区 count 字段与 jobs 长度一致。"""
        jobs = [
            _make_job(
                "strong1", age_fit=CandidateAgeFit.ELIGIBLE, within_3km=True, recommend_level="A"
            ),
            _make_job(
                "strong2", age_fit=CandidateAgeFit.ELIGIBLE, within_3km=True, recommend_level="B"
            ),
        ]
        sections = build_sections(jobs)
        strongly = sections[0]
        assert strongly.count == 2
        assert len(strongly.jobs) == 2


# ==================== build_summary ====================
class TestBuildSummary:
    """build_summary 汇总统计。"""

    def test_empty_summary(self) -> None:
        """空列表返回全 0 汇总。"""
        summary = build_summary([])
        assert summary.total == 0
        assert summary.strongly_recommend_count == 0
        assert summary.not_match_count == 0

    def test_section_counts(self) -> None:
        """分区计数正确。"""
        jobs = [
            _make_job(
                "strong", age_fit=CandidateAgeFit.ELIGIBLE, within_3km=True, recommend_level="A"
            ),
            _make_job("not_match", age_fit=CandidateAgeFit.INELIGIBLE),
        ]
        summary = build_summary(jobs)
        assert summary.total == 2
        assert summary.strongly_recommend_count == 1
        assert summary.not_match_count == 1

    def test_age_fit_counts(self) -> None:
        """年龄适配计数正确。"""
        jobs = [
            _make_job("eligible", age_fit=CandidateAgeFit.ELIGIBLE),
            _make_job("review", age_fit=CandidateAgeFit.REVIEW),
            _make_job("ineligible", age_fit=CandidateAgeFit.INELIGIBLE),
            _make_job("unknown", age_fit=CandidateAgeFit.UNKNOWN),
        ]
        summary = build_summary(jobs)
        assert summary.eligible_count == 1
        assert summary.review_count == 1
        assert summary.ineligible_count == 1
        assert summary.unknown_count == 1

    def test_within_3km_count(self) -> None:
        """3km 内计数正确。"""
        jobs = [
            _make_job("in", within_3km=True, age_fit=CandidateAgeFit.ELIGIBLE),
            _make_job("out", within_3km=False, age_fit=CandidateAgeFit.ELIGIBLE),
            _make_job("none", within_3km=None, age_fit=CandidateAgeFit.ELIGIBLE),
        ]
        summary = build_summary(jobs)
        assert summary.within_3km_count == 1

    def test_data_source_counts(self) -> None:
        """数据来源计数正确。"""
        jobs = [
            _make_job("detail", data_source="detail"),
            _make_job("list", data_source="list_only"),
        ]
        summary = build_summary(jobs)
        assert summary.detail_source_count == 1
        assert summary.list_only_source_count == 1
