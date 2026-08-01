"""P7 报告数据模型测试。

覆盖：
- ReportJob 字段默认值与校验
- ReportSummary 统计字段
- ReportMetadata 元数据
- ReportSection 与 ReportSectionType
- 模型禁用额外字段（extra=forbid）
- 空值安全降级
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from boss_tool.report.age_fit import CandidateAgeFit
from boss_tool.report.models import (
    ReportJob,
    ReportMetadata,
    ReportSection,
    ReportSectionType,
    ReportSummary,
)


class TestReportSectionType:
    """ReportSectionType 枚举测试。"""

    def test_enum_values(self) -> None:
        """枚举值正确。"""
        assert ReportSectionType.STRONGLY_RECOMMEND.value == "strongly_recommend"
        assert ReportSectionType.CONSIDER.value == "consider"
        assert ReportSectionType.MANUAL_REVIEW.value == "manual_review"
        assert ReportSectionType.NOT_MATCH.value == "not_match"

    def test_enum_is_str(self) -> None:
        """枚举继承 str。"""
        assert isinstance(ReportSectionType.STRONGLY_RECOMMEND, str)


class TestReportJob:
    """ReportJob 模型测试。"""

    def test_minimal_job(self) -> None:
        """最小必填字段构造。"""
        job = ReportJob(job_id="job-001")
        assert job.job_id == "job-001"
        assert job.title is None
        assert job.salary is None
        assert job.company is None
        assert job.benefits == []
        assert job.tags == []
        assert job.matched_rules == []
        assert job.failed_rules == []
        assert job.warnings == []
        assert job.explanations == []
        assert job.labor_intensity_tags == []
        assert job.score_breakdown == {}
        assert job.candidate_age_fit == CandidateAgeFit.UNKNOWN
        assert job.data_source == "list_only"

    def test_full_job(self) -> None:
        """完整字段构造。"""
        job = ReportJob(
            job_id="job-001",
            title="小区保洁",
            salary="3000-4000元/月",
            company="示例物业",
            location="杭州·拱墅区",
            distance_meter=1500.0,
            within_3km=True,
            score=85,
            recommend_level="A",
            job_category="保洁",
            age_status="no_limit",
            candidate_age_fit=CandidateAgeFit.ELIGIBLE,
            candidate_age_fit_reason="年龄不限，适合 60 岁候选人",
            data_source="detail",
        )
        assert job.job_id == "job-001"
        assert job.title == "小区保洁"
        assert job.distance_meter == 1500.0
        assert job.within_3km is True
        assert job.score == 85
        assert job.recommend_level == "A"
        assert job.candidate_age_fit == CandidateAgeFit.ELIGIBLE

    def test_job_id_required(self) -> None:
        """job_id 必填。"""
        with pytest.raises(ValidationError):
            ReportJob()  # type: ignore[call-arg]

    def test_job_id_empty_rejected(self) -> None:
        """job_id 不能为空字符串。"""
        with pytest.raises(ValidationError):
            ReportJob(job_id="")

    def test_extra_field_forbidden(self) -> None:
        """禁止额外字段。"""
        with pytest.raises(ValidationError):
            ReportJob(job_id="job-001", unknown_field="value")  # type: ignore[call-arg]

    def test_score_range_validation(self) -> None:
        """score 必须在 0..100。"""
        with pytest.raises(ValidationError):
            ReportJob(job_id="job-001", score=-1)
        with pytest.raises(ValidationError):
            ReportJob(job_id="job-001", score=101)

    def test_distance_non_negative(self) -> None:
        """distance_meter 必须非负。"""
        with pytest.raises(ValidationError):
            ReportJob(job_id="job-001", distance_meter=-100.0)

    def test_page_no_minimum(self) -> None:
        """page_no 必须 >= 1。"""
        with pytest.raises(ValidationError):
            ReportJob(job_id="job-001", page_no=0)

    def test_collected_at_datetime(self) -> None:
        """collected_at 接受 datetime。"""
        dt = datetime(2026, 8, 1, 10, 0, 0)
        job = ReportJob(job_id="job-001", collected_at=dt)
        assert job.collected_at == dt


class TestReportSummary:
    """ReportSummary 模型测试。"""

    def test_default_summary(self) -> None:
        """默认汇总全为 0。"""
        summary = ReportSummary()
        assert summary.total == 0
        assert summary.strongly_recommend_count == 0
        assert summary.consider_count == 0
        assert summary.manual_review_count == 0
        assert summary.not_match_count == 0
        assert summary.eligible_count == 0
        assert summary.review_count == 0
        assert summary.ineligible_count == 0
        assert summary.unknown_count == 0
        assert summary.within_3km_count == 0
        assert summary.detail_source_count == 0
        assert summary.list_only_source_count == 0

    def test_custom_summary(self) -> None:
        """自定义汇总。"""
        summary = ReportSummary(
            total=10,
            strongly_recommend_count=3,
            consider_count=2,
            manual_review_count=1,
            not_match_count=4,
        )
        assert summary.total == 10
        assert summary.strongly_recommend_count == 3

    def test_count_non_negative(self) -> None:
        """计数必须非负。"""
        with pytest.raises(ValidationError):
            ReportSummary(total=-1)


class TestReportMetadata:
    """ReportMetadata 模型测试。"""

    def test_default_metadata(self) -> None:
        """默认元数据。"""
        metadata = ReportMetadata()
        assert metadata.generated_at is not None
        assert metadata.db_filename == ""
        assert metadata.rule_version == "P6 v1"
        assert metadata.reference_location == ""
        assert metadata.distance_threshold_m == 3000.0
        assert metadata.candidate_age == 60
        assert metadata.safety_statement == ""

    def test_custom_metadata(self) -> None:
        """自定义元数据。"""
        dt = datetime(2026, 8, 1, 12, 0, 0)
        metadata = ReportMetadata(
            generated_at=dt,
            db_filename="boss.db",
            reference_location="杭州市拱墅区锦园小区",
            candidate_age=60,
        )
        assert metadata.generated_at == dt
        assert metadata.db_filename == "boss.db"
        assert metadata.reference_location == "杭州市拱墅区锦园小区"


class TestReportSection:
    """ReportSection 模型测试。"""

    def test_empty_section(self) -> None:
        """空分区。"""
        section = ReportSection(
            section_type=ReportSectionType.STRONGLY_RECOMMEND,
            title="强烈推荐",
            color="#27ae60",
        )
        assert section.jobs == []
        assert section.count == 0

    def test_section_with_jobs(self) -> None:
        """有岗位的分区。"""
        jobs = [
            ReportJob(job_id="job-001"),
            ReportJob(job_id="job-002"),
        ]
        section = ReportSection(
            section_type=ReportSectionType.STRONGLY_RECOMMEND,
            title="强烈推荐",
            color="#27ae60",
            jobs=jobs,
        )
        assert len(section.jobs) == 2
        assert section.count == 2

    def test_section_count_auto_sync(self) -> None:
        """count 字段在初始化后自动同步。"""
        jobs = [ReportJob(job_id=f"job-{i:03d}") for i in range(5)]
        section = ReportSection(
            section_type=ReportSectionType.CONSIDER,
            title="可考虑",
            color="#2980b9",
            jobs=jobs,
        )
        assert section.count == 5
