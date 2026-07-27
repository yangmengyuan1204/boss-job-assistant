"""数据模型与枚举测试。"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from boss_tool.enums import (
    AGE_TARGET_TO_MATCH,
    ActivityCategory,
    AgeMatchCategory,
    AgeTargetCategory,
    BoundaryRisk,
    Confidence,
    HiringLikelihood,
    JobActiveState,
    JobStatus,
    PhysicalIntensityCategory,
    RunStatus,
    SalaryUnit,
    ShiftType,
    SkipReason,
    StopReason,
    WalkingIntensity,
)
from boss_tool.models.age import AgeResult
from boss_tool.models.collection import CollectionMeta
from boss_tool.models.job import Job
from boss_tool.models.physical import PhysicalIntensityResult
from boss_tool.models.recruiter import RecruiterInfo
from boss_tool.models.run import RunRecord


# ==================== 枚举值正确性 ====================
class TestEnums:
    def test_age_target_category_values(self):
        assert AgeTargetCategory.EXACT_65_CAP.value == "exact_65_cap"
        assert AgeTargetCategory.RANGE_INCLUDES_60_TO_65.value == "range_includes_60_to_65"
        assert AgeTargetCategory.ALTERNATIVE_ACCEPTS_60.value == "alternative_accepts_60"
        assert AgeTargetCategory.BOUNDARY_60.value == "boundary_60"
        assert AgeTargetCategory.NO_EXPLICIT_AGE.value == "no_explicit_age"
        assert AgeTargetCategory.REJECTS_60.value == "rejects_60"

    def test_age_target_to_match_mapping(self):
        assert AGE_TARGET_TO_MATCH[AgeTargetCategory.EXACT_65_CAP] == AgeMatchCategory.ELIGIBLE
        assert (
            AGE_TARGET_TO_MATCH[AgeTargetCategory.RANGE_INCLUDES_60_TO_65]
            == AgeMatchCategory.ELIGIBLE
        )
        assert (
            AGE_TARGET_TO_MATCH[AgeTargetCategory.ALTERNATIVE_ACCEPTS_60]
            == AgeMatchCategory.ELIGIBLE
        )
        assert AGE_TARGET_TO_MATCH[AgeTargetCategory.BOUNDARY_60] == AgeMatchCategory.MANUAL_REVIEW
        assert (
            AGE_TARGET_TO_MATCH[AgeTargetCategory.NO_EXPLICIT_AGE] == AgeMatchCategory.MANUAL_REVIEW
        )
        assert AGE_TARGET_TO_MATCH[AgeTargetCategory.REJECTS_60] == AgeMatchCategory.INELIGIBLE

    def test_hiring_likelihood_values(self):
        assert HiringLikelihood.CONFIRMED.value == "confirmed"
        assert HiringLikelihood.LIKELY.value == "likely"
        assert HiringLikelihood.UNCERTAIN.value == "uncertain"
        assert HiringLikelihood.CLOSED.value == "closed"

    def test_skip_reason_values(self):
        assert SkipReason.LIST_FILTERED.value == "list_filtered"
        assert SkipReason.ALREADY_VISITED.value == "already_visited"
        assert SkipReason.CONTENT_UNCHANGED.value == "content_unchanged"
        assert SkipReason.AGE_REJECT_AT_LIST.value == "age_reject_at_list"

    def test_stop_reason_values(self):
        # 关键项不漏
        for v in [
            "completed",
            "budget_reached",
            "user_aborted",
            "browser_closed",
            "captcha",
            "slider_verification",
            "sms_verification",
            "login_expired",
            "security_page",
            "account_warning",
            "rate_limited",
            "http_403",
            "http_429",
            "redirect_loop",
            "page_structure_missing",
            "consecutive_parse_failures",
            "max_errors_reached",
            "unknown_error",
        ]:
            assert any(r.value == v for r in StopReason)

    def test_physical_intensity_categories(self):
        for v in ["low", "medium", "high", "unsuitable", "unknown"]:
            assert any(c.value == v for c in PhysicalIntensityCategory)

    def test_str_enum_serializes_to_value(self):
        # str 枚举应能直接当字符串使用
        assert str(AgeTargetCategory.EXACT_65_CAP) == "exact_65_cap"
        assert str(StopReason.CAPTCHA) == "captcha"

    def test_all_required_enums_exist(self):
        # 列表覆盖确保未漏
        enums = [
            AgeTargetCategory,
            AgeMatchCategory,
            BoundaryRisk,
            Confidence,
            PhysicalIntensityCategory,
            WalkingIntensity,
            ShiftType,
            ActivityCategory,
            HiringLikelihood,
            JobActiveState,
            JobStatus,
            SkipReason,
            StopReason,
            SalaryUnit,
            RunStatus,
        ]
        for e in enums:
            assert len(list(e)) >= 2


# ==================== Job 模型 ====================
class TestJobModel:
    def _valid_kwargs(self):
        return {
            "job_id": "J-001",
            "job_url": "https://www.zhipin.com/job/001",
            "job_title": "小区保安",
            "company_name": "某物业公司",
            "first_seen_at": datetime(2026, 7, 27, 10, 0, 0),
            "last_collected_at": datetime(2026, 7, 27, 10, 0, 0),
        }

    def test_minimal_job(self):
        job = Job(**self._valid_kwargs())
        assert job.job_id == "J-001"
        assert job.job_tags == []  # default_factory
        assert job.salary_min is None
        assert job.likely_still_hiring == HiringLikelihood.UNCERTAIN.value

    def test_empty_job_id_rejected(self):
        kwargs = self._valid_kwargs()
        kwargs["job_id"] = ""
        with pytest.raises(ValidationError):
            Job(**kwargs)

    def test_empty_job_url_rejected(self):
        kwargs = self._valid_kwargs()
        kwargs["job_url"] = "   "
        with pytest.raises(ValidationError, match="job_url"):
            Job(**kwargs)

    def test_salary_min_gt_max_rejected(self):
        kwargs = self._valid_kwargs()
        kwargs["salary_min"] = 5000
        kwargs["salary_max"] = 3000
        with pytest.raises(ValidationError, match="salary_min"):
            Job(**kwargs)

    def test_salary_negative_rejected(self):
        kwargs = self._valid_kwargs()
        kwargs["salary_min"] = -1
        with pytest.raises(ValidationError):
            Job(**kwargs)

    def test_invalid_longitude_rejected(self):
        kwargs = self._valid_kwargs()
        kwargs["longitude"] = 999.0
        with pytest.raises(ValidationError):
            Job(**kwargs)

    def test_invalid_latitude_rejected(self):
        kwargs = self._valid_kwargs()
        kwargs["latitude"] = -91.0
        with pytest.raises(ValidationError):
            Job(**kwargs)

    def test_extra_field_rejected(self):
        kwargs = self._valid_kwargs()
        kwargs["unknown_field"] = "x"
        with pytest.raises(ValidationError):
            Job(**kwargs)

    def test_job_tags_default_factory_no_shared_mutable(self):
        # 默认 list 必须是 default_factory，避免共享
        kw = self._valid_kwargs()
        j1 = Job(**kw)
        j2 = Job(**kw)
        j1.job_tags.append("tag1")
        assert j2.job_tags == []

    def test_job_tags_default_factory_explicit_assignment(self):
        # 显式赋值不被共享
        kw = self._valid_kwargs()
        j1 = Job(**kw)
        j1.job_tags = ["a", "b"]
        j2 = Job(**self._valid_kwargs())
        assert j2.job_tags == []


# ==================== AgeResult 模型 ====================
class TestAgeResultModel:
    def _kwargs(self, **overrides):
        defaults = {
            "candidate_age": 60,
            "age_evidence_raw": "65 岁以下",
            "age_target_category": AgeTargetCategory.EXACT_65_CAP,
            "age_match_category": AgeMatchCategory.ELIGIBLE,
            "is_exact_65_cap": True,
            "accepts_candidate_age": True,
            "boundary_risk": BoundaryRisk.NONE,
            "age_confidence": Confidence.HIGH,
            "age_needs_review": False,
        }
        defaults.update(overrides)
        return defaults

    def test_exact_65_cap_valid(self):
        r = AgeResult(**self._kwargs())
        assert r.age_target_category == AgeTargetCategory.EXACT_65_CAP.value
        assert r.is_exact_65_cap is True
        assert r.accepts_candidate_age is True

    def test_range_includes_60_65_valid(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.RANGE_INCLUDES_60_TO_65,
            age_match_category=AgeMatchCategory.ELIGIBLE,
            is_exact_65_cap=False,
            boundary_risk=BoundaryRisk.LOW,
            age_confidence=Confidence.HIGH,
            age_needs_review=False,
            accepts_candidate_age=True,
        )
        r = AgeResult(**kw)
        assert r.age_match_category == AgeMatchCategory.ELIGIBLE.value

    def test_alternative_accepts_60_valid(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.ALTERNATIVE_ACCEPTS_60,
            age_match_category=AgeMatchCategory.ELIGIBLE,
            is_exact_65_cap=False,
            boundary_risk=BoundaryRisk.LOW,
            age_confidence=Confidence.MEDIUM,
            age_needs_review=False,
            accepts_candidate_age=True,
        )
        r = AgeResult(**kw)
        assert r.accepts_candidate_age is True

    def test_boundary_60_valid(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.BOUNDARY_60,
            age_match_category=AgeMatchCategory.MANUAL_REVIEW,
            is_exact_65_cap=False,
            accepts_candidate_age=None,
            boundary_risk=BoundaryRisk.HIGH,
            age_confidence=Confidence.MEDIUM,
            age_needs_review=True,
        )
        r = AgeResult(**kw)
        assert r.age_needs_review is True
        assert r.accepts_candidate_age is None

    def test_no_explicit_age_valid(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.NO_EXPLICIT_AGE,
            age_match_category=AgeMatchCategory.MANUAL_REVIEW,
            is_exact_65_cap=False,
            accepts_candidate_age=None,
            boundary_risk=BoundaryRisk.MEDIUM,
            age_confidence=Confidence.LOW,
            age_needs_review=True,
        )
        r = AgeResult(**kw)
        assert r.accepts_candidate_age is None

    def test_rejects_60_valid(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.REJECTS_60,
            age_match_category=AgeMatchCategory.INELIGIBLE,
            is_exact_65_cap=False,
            accepts_candidate_age=False,
            boundary_risk=BoundaryRisk.NONE,
            age_confidence=Confidence.HIGH,
            age_needs_review=False,
        )
        r = AgeResult(**kw)
        assert r.accepts_candidate_age is False

    # ===== 矛盾组合 =====
    def test_exact_65_cap_without_is_flag_rejected(self):
        kw = self._kwargs(is_exact_65_cap=False)
        with pytest.raises(ValidationError, match="is_exact_65_cap"):
            AgeResult(**kw)

    def test_non_exact_65_with_is_flag_rejected(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.REJECTS_60,
            age_match_category=AgeMatchCategory.INELIGIBLE,
            is_exact_65_cap=True,
            accepts_candidate_age=False,
            age_needs_review=False,
        )
        with pytest.raises(ValidationError, match="is_exact_65_cap"):
            AgeResult(**kw)

    def test_rejects_60_accepts_true_rejected(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.REJECTS_60,
            age_match_category=AgeMatchCategory.INELIGIBLE,
            is_exact_65_cap=False,
            accepts_candidate_age=True,  # 与 rejects_60 矛盾
            age_needs_review=False,
        )
        with pytest.raises(ValidationError, match="accepts_candidate_age"):
            AgeResult(**kw)

    def test_rejects_60_needs_review_true_rejected(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.REJECTS_60,
            age_match_category=AgeMatchCategory.INELIGIBLE,
            is_exact_65_cap=False,
            accepts_candidate_age=False,
            age_needs_review=True,  # 与 rejects_60 矛盾
        )
        with pytest.raises(ValidationError, match="age_needs_review"):
            AgeResult(**kw)

    def test_boundary_60_needs_review_false_rejected(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.BOUNDARY_60,
            age_match_category=AgeMatchCategory.MANUAL_REVIEW,
            is_exact_65_cap=False,
            accepts_candidate_age=None,
            boundary_risk=BoundaryRisk.HIGH,
            age_confidence=Confidence.MEDIUM,
            age_needs_review=False,  # 与 boundary_60 矛盾
        )
        with pytest.raises(ValidationError, match="age_needs_review"):
            AgeResult(**kw)

    def test_boundary_60_accepts_not_none_rejected(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.BOUNDARY_60,
            age_match_category=AgeMatchCategory.MANUAL_REVIEW,
            is_exact_65_cap=False,
            accepts_candidate_age=True,  # 必须 None
            boundary_risk=BoundaryRisk.HIGH,
            age_confidence=Confidence.MEDIUM,
            age_needs_review=True,
        )
        with pytest.raises(ValidationError, match="accepts_candidate_age"):
            AgeResult(**kw)

    def test_eligible_with_accepts_none_rejected(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.EXACT_65_CAP,
            age_match_category=AgeMatchCategory.ELIGIBLE,
            is_exact_65_cap=True,
            accepts_candidate_age=None,  # 必须为 True
            boundary_risk=BoundaryRisk.NONE,
            age_confidence=Confidence.HIGH,
            age_needs_review=False,
        )
        with pytest.raises(ValidationError, match="accepts_candidate_age"):
            AgeResult(**kw)

    def test_mismatched_age_match_category_rejected(self):
        kw = self._kwargs(
            age_target_category=AgeTargetCategory.EXACT_65_CAP,
            age_match_category=AgeMatchCategory.INELIGIBLE,  # 应为 eligible
            is_exact_65_cap=True,
            accepts_candidate_age=False,
            age_needs_review=False,
        )
        with pytest.raises(ValidationError, match="age_match_category"):
            AgeResult(**kw)

    def test_age_min_gt_age_max_rejected(self):
        kw = self._kwargs(age_min=50, age_max=40)
        with pytest.raises(ValidationError, match="age_min"):
            AgeResult(**kw)


# ==================== PhysicalIntensityResult 模型 ====================
class TestPhysicalIntensityModel:
    def _kwargs(self, **overrides):
        defaults = {
            "physical_intensity_category": PhysicalIntensityCategory.LOW,
            "physical_needs_review": False,
        }
        defaults.update(overrides)
        return defaults

    def test_minimal_valid(self):
        r = PhysicalIntensityResult(**self._kwargs())
        assert r.physical_intensity_category == PhysicalIntensityCategory.LOW.value

    def test_score_within_range(self):
        r = PhysicalIntensityResult(**self._kwargs(physical_intensity_score=50))
        assert r.physical_intensity_score == 50

    def test_score_zero_ok(self):
        r = PhysicalIntensityResult(**self._kwargs(physical_intensity_score=0))
        assert r.physical_intensity_score == 0

    def test_score_hundred_ok(self):
        r = PhysicalIntensityResult(**self._kwargs(physical_intensity_score=100))
        assert r.physical_intensity_score == 100

    def test_score_negative_rejected(self):
        with pytest.raises(ValidationError):
            PhysicalIntensityResult(**self._kwargs(physical_intensity_score=-1))

    def test_score_over_100_rejected(self):
        with pytest.raises(ValidationError):
            PhysicalIntensityResult(**self._kwargs(physical_intensity_score=101))

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            PhysicalIntensityResult(**self._kwargs(unknown_field="x"))


# ==================== RecruiterInfo 模型 ====================
class TestRecruiterInfoModel:
    def test_minimal(self):
        r = RecruiterInfo()
        assert r.activity_category == ActivityCategory.UNKNOWN.value
        assert r.active_within_3d is None

    def test_with_values(self):
        r = RecruiterInfo(
            recruiter_name="王经理",
            recruiter_title="招聘主管",
            activity_raw="3 日内活跃",
            activity_category=ActivityCategory.ACTIVE_3D,
            active_within_3d=True,
        )
        assert r.recruiter_name == "王经理"
        assert r.active_within_3d is True


# ==================== CollectionMeta 模型 ====================
class TestCollectionMetaModel:
    def test_minimal(self):
        meta = CollectionMeta(
            source_page="https://www.zhipin.com/job/001",
            collected_at=datetime(2026, 7, 27, 10, 0, 0),
            parse_ok=True,
        )
        assert meta.parse_ok is True
        assert meta.visited_jobs is False
        assert meta.detail_visit_count == 0

    def test_default_factory_no_shared(self):
        m1 = CollectionMeta(source_page="a", collected_at=datetime.now(), parse_ok=True)
        m2 = CollectionMeta(source_page="b", collected_at=datetime.now(), parse_ok=True)
        m1.missing_fields.append("field")
        assert m2.missing_fields == []


# ==================== RunRecord 模型 ====================
class TestRunRecordModel:
    def test_minimal(self):
        r = RunRecord(
            run_id="20260727_1000_abc",
            started_at=datetime(2026, 7, 27, 10, 0, 0),
            status=RunStatus.RUNNING,
        )
        assert r.page_count == 0
        assert r.account_warning_detected is False

    def test_negative_count_rejected(self):
        with pytest.raises(ValidationError):
            RunRecord(
                run_id="x",
                started_at=datetime.now(),
                status=RunStatus.RUNNING,
                page_count=-1,
            )
