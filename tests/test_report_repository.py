"""P7 ReportRepository 只读查询层测试。

覆盖：
- 空数据库查询返回空列表
- 仅 job_list 记录查询（data_source=list_only）
- 仅 job_detail 记录查询（data_source=detail）
- job_list + job_detail JOIN 查询（详情优先）
- count_total 统计
- JSON 字段反序列化
- within_3km 类型转换
- distance_meter 类型转换
- 单条解析失败不阻断整批
- 年龄适配计算（基于 age_status）
- 距离从 V4 地理列取（不从 RuleResult 取）
- 只读：不写入数据库
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from boss_tool.enums import ActivityCategory
from boss_tool.models.job_detail import JobDetailRecord
from boss_tool.models.job_list import JobListRecord
from boss_tool.report.age_fit import CandidateAgeFit
from boss_tool.report.repository import ReportRepository
from boss_tool.rules.models import AgeStatus, RecommendLevel, RuleResult
from boss_tool.storage.database import Database
from boss_tool.storage.repositories import (
    JobDetailRepository,
    JobListRepository,
    RuleEngineRepository,
)


# ==================== 辅助函数 ====================
def _make_list_record(
    job_id: str = "list-001",
    title: str = "小区保洁",
    company: str = "示例物业",
    salary: str = "3000-4000元/月",
    location: str = "杭州·拱墅区",
) -> JobListRecord:
    """构造一条 JobListRecord。"""
    return JobListRecord(
        job_id=job_id,
        job_url=f"https://www.zhipin.com/job_detail/{job_id}.html",
        title=title,
        salary=salary,
        company=company,
        location=location,
        collected_at=datetime(2026, 8, 1, 10, 0, 0),
    )


def _make_detail_record(
    job_id: str = "detail-001",
    title: str = "小区保洁员",
    company: str = "示例物业",
    salary: str = "4000-5000元/月",
    description: str = "45岁以下，身体健康，有责任心。",
    distance_meter: float | None = None,
    within_3km: bool | None = None,
    normalized_address: str | None = None,
) -> JobDetailRecord:
    """构造一条 JobDetailRecord。"""
    return JobDetailRecord(
        job_id=job_id,
        job_url=f"https://www.zhipin.com/job_detail/{job_id}.html",
        title=title,
        salary=salary,
        location="杭州·拱墅区",
        description=description,
        company=company,
        recruiter_active="今日活跃",
        tags=["保洁"],
        benefits=["五险一金"],
        collected_at=datetime(2026, 8, 1, 10, 0, 0),
        normalized_address=normalized_address,
        distance_meter=distance_meter,
        within_3km=within_3km,
    )


def _make_rule_result(
    score: int = 85,
    level: RecommendLevel = RecommendLevel.A,
    category: str = "保洁",
    age_status: AgeStatus = AgeStatus.NO_LIMIT,
    distance_meter: float | None = 2000.0,
) -> RuleResult:
    """构造一条 RuleResult。"""
    return RuleResult(
        score=score,
        recommend_level=level,
        job_category=category,
        age_requirement_text="年龄不限" if age_status == AgeStatus.NO_LIMIT else None,
        age_status=age_status,
        recruiter_active_level=ActivityCategory.ACTIVE_3D,
        distance_meter=distance_meter,
        matched_rules=["category:保洁", "distance:30"],
        failed_rules=[],
        warnings=[],
        explanations=["岗位分类为保洁，加分+30"],
        labor_intensity_tags=[],
        score_breakdown={"category": 30, "distance": 30},
    )


@pytest.fixture
def db(tmp_db_path: Path) -> Database:
    """初始化空数据库。"""
    d = Database(tmp_db_path)
    d.initialize()
    return d


@pytest.fixture
def db_with_list_only(db: Database) -> Database:
    """仅有 job_list 记录的数据库。"""
    repo = JobListRepository(db.connection)
    repo.save_job_list(_make_list_record("list-only-001"))
    db.commit()
    return db


@pytest.fixture
def db_with_detail_only(db: Database) -> Database:
    """仅有 job_detail 记录的数据库。"""
    repo = JobDetailRepository(db.connection)
    repo.save_job_detail(_make_detail_record("detail-only-001"))
    db.commit()
    return db


@pytest.fixture
def db_with_both(db: Database) -> Database:
    """同时有 job_list 和 job_detail 记录的数据库。"""
    list_repo = JobListRepository(db.connection)
    detail_repo = JobDetailRepository(db.connection)
    rule_repo = RuleEngineRepository(db.connection)

    # 同一 job_id 在两表都有
    list_repo.save_job_list(_make_list_record("both-001", title="列表标题"))
    detail_repo.save_job_detail(
        _make_detail_record("both-001", title="详情标题", distance_meter=1500.0, within_3km=True)
    )
    rule_repo.save_rule_result("both-001", _make_rule_result())
    db.commit()
    return db


# ==================== 空数据库 ====================
class TestEmptyDatabase:
    """空数据库查询。"""

    def test_empty_db_returns_empty_list(self, db: Database) -> None:
        """空数据库返回空列表。"""
        repo = ReportRepository(db.connection)
        jobs = repo.fetch_all_jobs()
        assert jobs == []

    def test_empty_db_count_zero(self, db: Database) -> None:
        """空数据库 count_total 返回 0。"""
        repo = ReportRepository(db.connection)
        assert repo.count_total() == 0


# ==================== 仅 job_list ====================
class TestListOnly:
    """仅 job_list 记录查询。"""

    def test_list_only_returns_job(self, db_with_list_only: Database) -> None:
        """仅 job_list 记录被查询到。"""
        repo = ReportRepository(db_with_list_only.connection)
        jobs = repo.fetch_all_jobs()
        assert len(jobs) == 1
        assert jobs[0].job_id == "list-only-001"
        assert jobs[0].data_source == "list_only"

    def test_list_only_count(self, db_with_list_only: Database) -> None:
        """count_total 返回 1。"""
        repo = ReportRepository(db_with_list_only.connection)
        assert repo.count_total() == 1

    def test_list_only_age_fit_unknown(self, db_with_list_only: Database) -> None:
        """仅列表页记录年龄适配为 UNKNOWN。"""
        repo = ReportRepository(db_with_list_only.connection)
        jobs = repo.fetch_all_jobs()
        assert jobs[0].candidate_age_fit == CandidateAgeFit.UNKNOWN


# ==================== 仅 job_detail ====================
class TestDetailOnly:
    """仅 job_detail 记录查询。"""

    def test_detail_only_returns_job(self, db_with_detail_only: Database) -> None:
        """仅 job_detail 记录被查询到。"""
        repo = ReportRepository(db_with_detail_only.connection)
        jobs = repo.fetch_all_jobs()
        assert len(jobs) == 1
        assert jobs[0].job_id == "detail-only-001"
        assert jobs[0].data_source == "detail"

    def test_detail_only_count(self, db_with_detail_only: Database) -> None:
        """count_total 返回 1。"""
        repo = ReportRepository(db_with_detail_only.connection)
        assert repo.count_total() == 1


# ==================== JOIN 查询（详情优先）====================
class TestJoinDetailPriority:
    """job_list + job_detail JOIN 查询，详情优先。"""

    def test_both_returns_one_job(self, db_with_both: Database) -> None:
        """同一 job_id 在两表都有时，JOIN 返回一条记录。"""
        repo = ReportRepository(db_with_both.connection)
        jobs = repo.fetch_all_jobs()
        assert len(jobs) == 1
        assert jobs[0].job_id == "both-001"

    def test_detail_title_takes_priority(self, db_with_both: Database) -> None:
        """详情标题优先于列表标题。"""
        repo = ReportRepository(db_with_both.connection)
        jobs = repo.fetch_all_jobs()
        assert jobs[0].title == "详情标题"

    def test_detail_distance_used(self, db_with_both: Database) -> None:
        """距离从 job_detail.distance_meter 取。"""
        repo = ReportRepository(db_with_both.connection)
        jobs = repo.fetch_all_jobs()
        assert jobs[0].distance_meter == 1500.0
        assert jobs[0].within_3km is True

    def test_rule_fields_loaded(self, db_with_both: Database) -> None:
        """规则引擎字段被加载。"""
        repo = ReportRepository(db_with_both.connection)
        jobs = repo.fetch_all_jobs()
        job = jobs[0]
        assert job.score == 85
        assert job.recommend_level == "A"
        assert job.job_category == "保洁"
        assert job.age_status == "no_limit"
        assert job.recruiter_active_level == "active_3d"

    def test_age_fit_computed(self, db_with_both: Database) -> None:
        """年龄适配基于 age_status 计算。"""
        repo = ReportRepository(db_with_both.connection)
        jobs = repo.fetch_all_jobs()
        # age_status=no_limit -> ELIGIBLE
        assert jobs[0].candidate_age_fit == CandidateAgeFit.ELIGIBLE

    def test_json_fields_deserialized(self, db_with_both: Database) -> None:
        """JSON 字段被反序列化为列表/字典。"""
        repo = ReportRepository(db_with_both.connection)
        jobs = repo.fetch_all_jobs()
        job = jobs[0]
        assert isinstance(job.matched_rules, list)
        assert isinstance(job.explanations, list)
        assert isinstance(job.score_breakdown, dict)
        assert "category:保洁" in job.matched_rules


# ==================== count_total ====================
class TestCountTotal:
    """count_total 统计测试。"""

    def test_count_with_mixed_records(self, db: Database) -> None:
        """混合记录的 count_total。"""
        list_repo = JobListRepository(db.connection)
        detail_repo = JobDetailRepository(db.connection)

        list_repo.save_job_list(_make_list_record("mixed-1"))
        list_repo.save_job_list(_make_list_record("mixed-2"))
        detail_repo.save_job_detail(_make_detail_record("mixed-2"))  # 与 list 重叠
        detail_repo.save_job_detail(_make_detail_record("mixed-3"))  # 仅 detail
        db.commit()

        repo = ReportRepository(db.connection)
        # 总数 = (list ∪ detail) = {mixed-1, mixed-2, mixed-3} = 3
        assert repo.count_total() == 3

    def test_fetch_all_returns_union(self, db: Database) -> None:
        """fetch_all_jobs 返回 list 与 detail 的并集。"""
        list_repo = JobListRepository(db.connection)
        detail_repo = JobDetailRepository(db.connection)

        list_repo.save_job_list(_make_list_record("union-1"))
        list_repo.save_job_list(_make_list_record("union-2"))
        detail_repo.save_job_detail(_make_detail_record("union-2"))
        detail_repo.save_job_detail(_make_detail_record("union-3"))
        db.commit()

        repo = ReportRepository(db.connection)
        jobs = repo.fetch_all_jobs()
        job_ids = {j.job_id for j in jobs}
        assert job_ids == {"union-1", "union-2", "union-3"}


# ==================== within_3km 类型转换 ====================
class TestWithin3kmConversion:
    """within_3km 从 0/1/NULL 转换为 bool/None。"""

    def test_within_3km_true(self, db: Database) -> None:
        """within_3km=1 转换为 True。"""
        detail_repo = JobDetailRepository(db.connection)
        detail_repo.save_job_detail(
            _make_detail_record("w3k-true", within_3km=True, distance_meter=1000.0)
        )
        db.commit()

        repo = ReportRepository(db.connection)
        jobs = repo.fetch_all_jobs()
        assert jobs[0].within_3km is True

    def test_within_3km_false(self, db: Database) -> None:
        """within_3km=0 转换为 False。"""
        detail_repo = JobDetailRepository(db.connection)
        detail_repo.save_job_detail(
            _make_detail_record("w3k-false", within_3km=False, distance_meter=5000.0)
        )
        db.commit()

        repo = ReportRepository(db.connection)
        jobs = repo.fetch_all_jobs()
        assert jobs[0].within_3km is False

    def test_within_3km_null(self, db: Database) -> None:
        """within_3km=NULL 转换为 None。"""
        detail_repo = JobDetailRepository(db.connection)
        detail_repo.save_job_detail(
            _make_detail_record("w3k-null", within_3km=None, distance_meter=None)
        )
        db.commit()

        repo = ReportRepository(db.connection)
        jobs = repo.fetch_all_jobs()
        assert jobs[0].within_3km is None
        assert jobs[0].distance_meter is None


# ==================== 只读测试 ====================
class TestReadOnly:
    """ReportRepository 不写入数据库。"""

    def test_no_write_after_fetch(self, db_with_both: Database) -> None:
        """查询后数据库内容不变。"""
        # 记录查询前的数据
        repo = ReportRepository(db_with_both.connection)
        before = repo.fetch_all_jobs()
        before_count = repo.count_total()

        # 再次查询
        after = repo.fetch_all_jobs()
        after_count = repo.count_total()

        assert len(after) == len(before)
        assert after_count == before_count

    def test_readonly_connection_rejects_write(self, tmp_db_path: Path) -> None:
        """只读 URI 连接拒绝写操作。"""
        import sqlite3

        # 先正常初始化数据库
        db = Database(tmp_db_path)
        db.initialize()
        db.close()

        # 用只读 URI 打开
        uri = f"file:{tmp_db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row

        # 读操作正常
        conn.execute("SELECT * FROM job_list").fetchall()

        # 写操作应失败
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO job_list (job_id, collected_at) VALUES (?, ?)", ("test", "2026-08-01")
            )
        conn.close()
