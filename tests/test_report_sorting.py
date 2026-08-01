"""P7 多级排序规则测试。

覆盖：
- 7 级优先级排序顺序
- ELIGIBLE > REVIEW > UNKNOWN > INELIGIBLE
- within_3km: True > None > False
- recommend_level: A > B > C > D
- score: 高 > 低
- distance_meter: 近 > 远，None 最后
- recruiter_active_level: 3日内 > 本周 > 其他
- collected_at: 新 > 旧
- None 值安全降级
- 稳定排序：同分保持原顺序
- 空列表排序
"""

from __future__ import annotations

from datetime import datetime

from boss_tool.report.age_fit import CandidateAgeFit
from boss_tool.report.models import ReportJob
from boss_tool.report.sorting import sort_jobs


def _make_job(
    job_id: str = "job-001",
    age_fit: CandidateAgeFit = CandidateAgeFit.UNKNOWN,
    within_3km: bool | None = None,
    recommend_level: str | None = None,
    score: int | None = None,
    distance_meter: float | None = None,
    recruiter_active_level: str | None = None,
    collected_at: datetime | None = None,
) -> ReportJob:
    """构造测试用 ReportJob。"""
    return ReportJob(
        job_id=job_id,
        candidate_age_fit=age_fit,
        within_3km=within_3km,
        recommend_level=recommend_level,
        score=score,
        distance_meter=distance_meter,
        recruiter_active_level=recruiter_active_level,
        collected_at=collected_at,
    )


class TestAgeFitPriority:
    """年龄适配优先级：ELIGIBLE > REVIEW > UNKNOWN > INELIGIBLE。"""

    def test_eligible_before_review(self) -> None:
        """ELIGIBLE 排在 REVIEW 之前。"""
        job_eligible = _make_job("eligible", age_fit=CandidateAgeFit.ELIGIBLE)
        job_review = _make_job("review", age_fit=CandidateAgeFit.REVIEW)
        sorted_jobs = sort_jobs([job_review, job_eligible])
        assert sorted_jobs[0].job_id == "eligible"
        assert sorted_jobs[1].job_id == "review"

    def test_review_before_unknown(self) -> None:
        """REVIEW 排在 UNKNOWN 之前。"""
        job_review = _make_job("review", age_fit=CandidateAgeFit.REVIEW)
        job_unknown = _make_job("unknown", age_fit=CandidateAgeFit.UNKNOWN)
        sorted_jobs = sort_jobs([job_unknown, job_review])
        assert sorted_jobs[0].job_id == "review"

    def test_unknown_before_ineligible(self) -> None:
        """UNKNOWN 排在 INELIGIBLE 之前。"""
        job_unknown = _make_job("unknown", age_fit=CandidateAgeFit.UNKNOWN)
        job_ineligible = _make_job("ineligible", age_fit=CandidateAgeFit.INELIGIBLE)
        sorted_jobs = sort_jobs([job_ineligible, job_unknown])
        assert sorted_jobs[0].job_id == "unknown"

    def test_full_priority_order(self) -> None:
        """完整优先级顺序：ELIGIBLE > REVIEW > UNKNOWN > INELIGIBLE。"""
        jobs = [
            _make_job("ineligible", age_fit=CandidateAgeFit.INELIGIBLE),
            _make_job("unknown", age_fit=CandidateAgeFit.UNKNOWN),
            _make_job("review", age_fit=CandidateAgeFit.REVIEW),
            _make_job("eligible", age_fit=CandidateAgeFit.ELIGIBLE),
        ]
        sorted_jobs = sort_jobs(jobs)
        assert [j.job_id for j in sorted_jobs] == ["eligible", "review", "unknown", "ineligible"]


class TestWithin3kmPriority:
    """3 公里内优先级：True > None > False。"""

    def test_true_before_false(self) -> None:
        """True 排在 False 之前。"""
        job_true = _make_job("true", within_3km=True, age_fit=CandidateAgeFit.ELIGIBLE)
        job_false = _make_job("false", within_3km=False, age_fit=CandidateAgeFit.ELIGIBLE)
        sorted_jobs = sort_jobs([job_false, job_true])
        assert sorted_jobs[0].job_id == "true"

    def test_none_between_true_and_false(self) -> None:
        """None 排在 True 和 False 之间。"""
        jobs = [
            _make_job("false", within_3km=False, age_fit=CandidateAgeFit.ELIGIBLE),
            _make_job("none", within_3km=None, age_fit=CandidateAgeFit.ELIGIBLE),
            _make_job("true", within_3km=True, age_fit=CandidateAgeFit.ELIGIBLE),
        ]
        sorted_jobs = sort_jobs(jobs)
        assert [j.job_id for j in sorted_jobs] == ["true", "none", "false"]


class TestRecommendLevelPriority:
    """推荐等级优先级：A > B > C > D。"""

    def test_level_order(self) -> None:
        """推荐等级顺序正确。"""
        jobs = [
            _make_job("D", recommend_level="D", age_fit=CandidateAgeFit.ELIGIBLE, within_3km=True),
            _make_job("B", recommend_level="B", age_fit=CandidateAgeFit.ELIGIBLE, within_3km=True),
            _make_job("A", recommend_level="A", age_fit=CandidateAgeFit.ELIGIBLE, within_3km=True),
            _make_job("C", recommend_level="C", age_fit=CandidateAgeFit.ELIGIBLE, within_3km=True),
        ]
        sorted_jobs = sort_jobs(jobs)
        assert [j.job_id for j in sorted_jobs] == ["A", "B", "C", "D"]

    def test_none_level_last(self) -> None:
        """None 等级排在最后。"""
        jobs = [
            _make_job(
                "none", recommend_level=None, age_fit=CandidateAgeFit.ELIGIBLE, within_3km=True
            ),
            _make_job("D", recommend_level="D", age_fit=CandidateAgeFit.ELIGIBLE, within_3km=True),
        ]
        sorted_jobs = sort_jobs(jobs)
        assert sorted_jobs[0].job_id == "D"


class TestScorePriority:
    """总分优先级：高 > 低。"""

    def test_high_score_first(self) -> None:
        """高分排在前面。"""
        jobs = [
            _make_job(
                "low",
                score=50,
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
            ),
            _make_job(
                "high",
                score=90,
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
            ),
        ]
        sorted_jobs = sort_jobs(jobs)
        assert sorted_jobs[0].job_id == "high"

    def test_none_score_as_zero(self) -> None:
        """None score 视为 0。"""
        jobs = [
            _make_job(
                "none",
                score=None,
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
            ),
            _make_job(
                "zero",
                score=0,
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
            ),
        ]
        sorted_jobs = sort_jobs(jobs)
        # 两者同分，稳定排序保持原顺序
        assert sorted_jobs[0].job_id == "none"


class TestDistancePriority:
    """距离优先级：近 > 远，None 最后。"""

    def test_near_first(self) -> None:
        """近距离排在前面。"""
        jobs = [
            _make_job(
                "far",
                distance_meter=5000.0,
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=90,
            ),
            _make_job(
                "near",
                distance_meter=1000.0,
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=90,
            ),
        ]
        sorted_jobs = sort_jobs(jobs)
        assert sorted_jobs[0].job_id == "near"

    def test_none_distance_last(self) -> None:
        """None 距离排在最后。"""
        jobs = [
            _make_job(
                "none",
                distance_meter=None,
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=90,
            ),
            _make_job(
                "has",
                distance_meter=5000.0,
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=90,
            ),
        ]
        sorted_jobs = sort_jobs(jobs)
        assert sorted_jobs[0].job_id == "has"


class TestRecruiterActivePriority:
    """招聘者活跃优先级：3日内 > 本周 > 其他 > 未知。"""

    def test_active_3d_first(self) -> None:
        """3日内活跃排在前面。"""
        jobs = [
            _make_job(
                "week",
                recruiter_active_level="active_this_week",
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=90,
                distance_meter=1000.0,
            ),
            _make_job(
                "3d",
                recruiter_active_level="active_3d",
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=90,
                distance_meter=1000.0,
            ),
        ]
        sorted_jobs = sort_jobs(jobs)
        assert sorted_jobs[0].job_id == "3d"


class TestCollectedAtPriority:
    """采集时间优先级：新 > 旧。"""

    def test_newer_first(self) -> None:
        """新采集的排在前面。"""
        jobs = [
            _make_job(
                "old",
                collected_at=datetime(2026, 7, 1),
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=90,
                distance_meter=1000.0,
                recruiter_active_level="active_3d",
            ),
            _make_job(
                "new",
                collected_at=datetime(2026, 8, 1),
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=90,
                distance_meter=1000.0,
                recruiter_active_level="active_3d",
            ),
        ]
        sorted_jobs = sort_jobs(jobs)
        assert sorted_jobs[0].job_id == "new"


class TestStableSort:
    """稳定排序：同分保持原顺序。"""

    def test_same_keys_preserve_order(self) -> None:
        """相同排序键保持原顺序。"""
        jobs = [
            _make_job(
                "first",
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=90,
                distance_meter=1000.0,
                recruiter_active_level="active_3d",
                collected_at=datetime(2026, 8, 1),
            ),
            _make_job(
                "second",
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=90,
                distance_meter=1000.0,
                recruiter_active_level="active_3d",
                collected_at=datetime(2026, 8, 1),
            ),
            _make_job(
                "third",
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=90,
                distance_meter=1000.0,
                recruiter_active_level="active_3d",
                collected_at=datetime(2026, 8, 1),
            ),
        ]
        sorted_jobs = sort_jobs(jobs)
        assert [j.job_id for j in sorted_jobs] == ["first", "second", "third"]


class TestEdgeCases:
    """边界情况。"""

    def test_empty_list(self) -> None:
        """空列表排序返回空列表。"""
        assert sort_jobs([]) == []

    def test_single_job(self) -> None:
        """单条岗位排序返回单条。"""
        job = _make_job("only")
        sorted_jobs = sort_jobs([job])
        assert len(sorted_jobs) == 1
        assert sorted_jobs[0].job_id == "only"

    def test_does_not_modify_original(self) -> None:
        """排序不修改原列表。"""
        jobs = [
            _make_job("b", age_fit=CandidateAgeFit.INELIGIBLE),
            _make_job("a", age_fit=CandidateAgeFit.ELIGIBLE),
        ]
        original_order = [j.job_id for j in jobs]
        sort_jobs(jobs)
        assert [j.job_id for j in jobs] == original_order


class TestFullSortScenario:
    """完整排序场景。"""

    def test_mixed_jobs_sort_order(self) -> None:
        """混合岗位的完整排序。"""
        jobs = [
            # INELIGIBLE + 远距离 → 应排最后
            _make_job(
                "ineligible-far",
                age_fit=CandidateAgeFit.INELIGIBLE,
                within_3km=False,
                recommend_level="D",
                score=20,
                distance_meter=10000.0,
            ),
            # ELIGIBLE + 3km内 + A级 + 高分 + 近距离 → 应排第一
            _make_job(
                "best",
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=95,
                distance_meter=500.0,
                recruiter_active_level="active_3d",
                collected_at=datetime(2026, 8, 1),
            ),
            # ELIGIBLE + 3km内 + A级 + 中等分 → 应排第二
            _make_job(
                "good",
                age_fit=CandidateAgeFit.ELIGIBLE,
                within_3km=True,
                recommend_level="A",
                score=85,
                distance_meter=1500.0,
                recruiter_active_level="active_3d",
                collected_at=datetime(2026, 8, 1),
            ),
            # UNKNOWN → 应排第三
            _make_job(
                "unknown",
                age_fit=CandidateAgeFit.UNKNOWN,
            ),
        ]
        sorted_jobs = sort_jobs(jobs)
        assert [j.job_id for j in sorted_jobs] == ["best", "good", "unknown", "ineligible-far"]
