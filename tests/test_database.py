"""数据库与 Repository 测试。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from boss_tool.enums import (
    HiringLikelihood,
    JobActiveState,
    JobStatus,
    RunStatus,
    StopReason,
)
from boss_tool.models.collection import CollectionMeta
from boss_tool.models.job import Job
from boss_tool.models.run import RunRecord
from boss_tool.storage.database import CURRENT_SCHEMA_VERSION, Database
from boss_tool.storage.repositories import (
    GeocodeCacheRepository,
    JobRepository,
    RunLogRepository,
)


# ==================== Database 初始化 ====================
class TestDatabaseInit:
    def test_first_init_success(self, tmp_db_path: Path):
        db = Database(tmp_db_path, foreign_keys=True)
        db.initialize()
        assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
        db.close()

    def test_repeat_init_no_data_loss(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        # 插入一行
        repo = JobRepository(db.connection)
        job = _make_job("J1", "https://x/j1")
        repo.upsert(job)
        db.commit()  # 显式提交，确保数据持久化
        db.close()

        # 再次初始化
        db2 = Database(tmp_db_path)
        db2.initialize()
        repo2 = JobRepository(db2.connection)
        got = repo2.get_by_id("J1")
        assert got is not None
        assert got.job_title == job.job_title
        db2.close()

    def test_schema_version_correct(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
        db.close()

    def test_foreign_keys_pragma_on(self, tmp_db_path: Path):
        db = Database(tmp_db_path, foreign_keys=True)
        db.initialize()
        cur = db.connection.execute("PRAGMA foreign_keys;")
        row = cur.fetchone()
        assert row[0] == 1
        db.close()

    def test_foreign_keys_off_when_disabled(self, tmp_db_path: Path):
        db = Database(tmp_db_path, foreign_keys=False)
        db.initialize()
        cur = db.connection.execute("PRAGMA foreign_keys;")
        row = cur.fetchone()
        assert row[0] == 0
        db.close()


# ==================== JobRepository ====================
class TestJobRepository:
    def test_upsert_new_job(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        job = _make_job("J1", "https://x/j1")
        repo.upsert(job)
        got = repo.get_by_id("J1")
        assert got is not None
        assert got.job_url == "https://x/j1"
        assert got.job_title == "小区保安"
        db.close()

    def test_upsert_update_existing(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        job = _make_job("J1", "https://x/j1", salary_min=3000, salary_max=5000)
        repo.upsert(job)
        # 更新（使用 updated 状态）
        updated = _make_job(
            "J1", "https://x/j1", salary_min=4000, salary_max=6000, job_status=JobStatus.UPDATED
        )
        repo.upsert(updated)
        got = repo.get_by_id("J1")
        assert got is not None
        assert got.salary_min == 4000
        assert got.salary_max == 6000
        # job_status 由调用方决定，不写死
        assert got.job_status == JobStatus.UPDATED.value
        db.close()

    def test_upsert_preserves_first_seen_at(self, tmp_db_path: Path):
        """P0.1 新增：upsert 更新时 first_seen_at 不被覆盖。"""
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        first_seen = datetime(2026, 7, 1, 10, 0, 0)
        job = _make_job("J1", "https://x/j1", first_seen_at=first_seen)
        repo.upsert(job)
        db.commit()

        # 第二次 upsert，first_seen_at 不同
        later_first_seen = datetime(2026, 7, 27, 10, 0, 0)
        updated = _make_job(
            "J1",
            "https://x/j1",
            first_seen_at=later_first_seen,
            last_collected_at=datetime(2026, 7, 27, 11, 0, 0),
        )
        repo.upsert(updated)
        db.commit()

        got = repo.get_by_id("J1")
        assert got is not None
        # first_seen_at 保持原值
        assert got.first_seen_at == first_seen
        # last_collected_at 更新为新值
        assert got.last_collected_at == datetime(2026, 7, 27, 11, 0, 0)
        db.close()

    def test_upsert_does_not_force_updated_status(self, tmp_db_path: Path):
        """P0.1 新增：重复 upsert 不应自动把 job_status 变成 updated。

        调用方传 active 时保持 active；调用方传 updated 时才为 updated。
        """
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        # 初始 active
        repo.upsert(_make_job("J1", "https://x/j1", job_status=JobStatus.ACTIVE))
        db.commit()
        # 再次 upsert 同样 active（不传 updated）
        repo.upsert(_make_job("J1", "https://x/j1", job_status=JobStatus.ACTIVE))
        db.commit()
        got = repo.get_by_id("J1")
        assert got is not None
        assert got.job_status == JobStatus.ACTIVE.value
        db.close()

    def test_job_url_conflict_reuses_existing_job_id(self, tmp_db_path: Path):
        """P0.1 新增：URL 冲突时复用数据库已有 job_id，不创建重复岗位。

        策略：upsert 前先按 job_url 查询。
        - URL 已存在且 job_id 不同 → 使用数据库已有 job_id
        - 不创建重复岗位
        - 不静默吞掉 IntegrityError
        """
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)

        # 插入 job_id=A, job_url=X
        repo.upsert(_make_job("A", "https://x/j1", job_title="岗位A"))
        db.commit()

        # 再插入 job_id=B, job_url=X（相同 URL）
        # 应复用 job_id=A，而非抛 IntegrityError
        job_b = _make_job("B", "https://x/j1", job_title="岗位B")
        returned = repo.upsert(job_b)
        db.commit()

        # 返回的 job_id 应为数据库已有 A
        assert returned.job_id == "A"

        # 数据库中应只有 1 条记录（job_id=A）
        rows = db.connection.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()
        assert rows["c"] == 1

        # 内容应为最新 upsert 的（岗位B）
        got = repo.get_by_id("A")
        assert got is not None
        assert got.job_title == "岗位B"
        # URL 已存在，按 URL 查询也应返回同一记录
        got_by_url = repo.get_by_url("https://x/j1")
        assert got_by_url is not None
        assert got_by_url.job_id == "A"
        db.close()

    def test_get_by_id_returns_none_if_missing(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        assert repo.get_by_id("not-exist") is None
        db.close()

    def test_get_by_url(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        job = _make_job("J1", "https://x/j1")
        repo.upsert(job)
        db.commit()
        got = repo.get_by_url("https://x/j1")
        assert got is not None
        assert got.job_id == "J1"
        db.close()

    def test_get_by_url_returns_none_if_missing(self, tmp_db_path: Path):
        """P0.1 新增：URL 不存在时返回 None。"""
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        assert repo.get_by_url("https://x/nonexistent") is None
        db.close()


# ==================== 事务回滚 ====================
class TestTransaction:
    def test_transaction_rollback_on_exception(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)

        def _raise_inside_transaction():
            with db.transaction():
                repo.upsert(_make_job("J1", "https://x/j1"))
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            _raise_inside_transaction()
        # 回滚后岗位不应存在
        assert repo.get_by_id("J1") is None
        db.close()

    def test_transaction_commit_on_success(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        with db.transaction():
            repo.upsert(_make_job("J1", "https://x/j1"))
        assert repo.get_by_id("J1") is not None
        db.close()


# ==================== 外键约束 ====================
class TestForeignKey:
    def test_collection_meta_fk(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        from boss_tool.storage.repositories import CollectionMetaRepository

        repo = CollectionMetaRepository(db.connection)
        meta = CollectionMeta(
            source_page="https://x/j1",
            collected_at=datetime.now(),
            parse_ok=True,
        )
        # job_id 不存在时应触发外键错误
        with pytest.raises(sqlite3.IntegrityError):
            repo.create("non-existent-job", meta)
        db.close()

    def test_job_snapshot_fk(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        cur = db.connection
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                "INSERT INTO job_snapshots (job_id, snapshot_at, snapshot_json) VALUES (?, ?, ?)",
                ("non-existent", datetime.now().isoformat(), "{}"),
            )
        db.close()


# ==================== RunLogRepository ====================
class TestRunLogRepository:
    def test_create_and_finish(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = RunLogRepository(db.connection)

        started = datetime(2026, 7, 27, 10, 0, 0)
        ended = started + timedelta(minutes=10)
        record = RunRecord(
            run_id="run-001",
            started_at=started,
            status=RunStatus.RUNNING,
        )
        repo.create(record)
        db.commit()

        # 更新结束态
        record.ended_at = ended
        record.status = RunStatus.COMPLETED
        record.stop_reason = StopReason.COMPLETED
        record.run_duration_seconds = 600
        repo.finish(record)
        db.commit()

        # 查询
        row = db.connection.execute(
            "SELECT * FROM run_logs WHERE run_id = ?", ("run-001",)
        ).fetchone()
        assert row is not None
        assert row["status"] == RunStatus.COMPLETED.value
        assert row["stop_reason"] == StopReason.COMPLETED.value
        # status=completed 时 run_completed 应为 1
        assert row["run_completed"] == 1
        assert row["run_duration_seconds"] == 600
        db.close()

    def test_run_id_unique(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = RunLogRepository(db.connection)
        r = RunRecord(run_id="dup", started_at=datetime.now(), status=RunStatus.RUNNING)
        repo.create(r)
        with pytest.raises(sqlite3.IntegrityError):
            repo.create(r)
        db.close()


# ==================== GeocodeCacheRepository ====================
class TestGeocodeCacheRepository:
    def test_upsert_and_get(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = GeocodeCacheRepository(db.connection)
        repo.upsert(
            query_text="建国北路锦园小区",
            standardized="浙江省杭州市拱墅区建国北路锦园小区",
            longitude=120.1769,
            latitude=30.2761,
            district="拱墅区",
            fetched_at=datetime.now(),
        )
        row = repo.get("建国北路锦园小区")
        assert row is not None
        assert row["district"] == "拱墅区"
        db.close()

    def test_upsert_overwrite(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = GeocodeCacheRepository(db.connection)
        repo.upsert("addr", "s1", 1.0, 2.0, "d1", datetime.now())
        repo.upsert("addr", "s2", 3.0, 4.0, "d2", datetime.now())
        row = repo.get("addr")
        assert row["standardized"] == "s2"
        assert row["longitude"] == 3.0
        db.close()


# ==================== 测试不污染真实数据库 ====================
class TestIsolation:
    def test_uses_tmp_db_not_real_data(self, tmp_db_path: Path):
        # tmp_db_path 在临时目录下，确保不是项目 data/
        assert "boss_test_" in str(tmp_db_path) or "pytest" in str(tmp_db_path).lower()
        assert not str(tmp_db_path).startswith(str(Path(__file__).resolve().parent.parent / "data"))


# ==================== Helper ====================
def _make_job(job_id: str, job_url: str, **overrides) -> Job:
    defaults = {
        "job_id": job_id,
        "job_url": job_url,
        "job_title": "小区保安",
        "company_name": "某物业公司",
        "salary_raw": "3000-5000元/月",
        "first_seen_at": datetime(2026, 7, 27, 10, 0, 0),
        "last_collected_at": datetime(2026, 7, 27, 10, 0, 0),
        "job_active_state": JobActiveState.OPEN,
        "likely_still_hiring": HiringLikelihood.UNCERTAIN,
        "job_status": JobStatus.ACTIVE,
    }
    defaults.update(overrides)
    return Job(**defaults)


def _make_full_job(job_id: str = "FULL-1", job_url: str = "https://x/full") -> Job:
    """构造包含所有嵌套对象与评分字段的完整 Job。"""
    from boss_tool.enums import (
        ActivityCategory,
        AgeMatchCategory,
        AgeTargetCategory,
        BoundaryRisk,
        Confidence,
        PhysicalIntensityCategory,
        ShiftType,
        WalkingIntensity,
    )
    from boss_tool.models.age import AgeResult
    from boss_tool.models.collection import CollectionMeta
    from boss_tool.models.physical import PhysicalIntensityResult
    from boss_tool.models.recruiter import RecruiterInfo

    return Job(
        job_id=job_id,
        job_url=job_url,
        job_title="高档小区保安",
        company_name="某高端物业公司",
        salary_raw="5000-7000元/月",
        salary_min=5000,
        salary_max=7000,
        salary_months=12,
        experience="1-3年",
        degree="初中",
        job_tags=["包吃", "包住", "五险"],
        job_desc_full="负责小区门岗登记、巡逻等工作",
        job_desc_summary="门岗与巡逻",
        address_raw="杭州市拱墅区建国北路",
        address_std="浙江省杭州市拱墅区建国北路",
        district="拱墅区",
        longitude=120.1769,
        latitude=30.2761,
        distance_m=850.5,
        within_3km=True,
        publish_time_raw="2026-07-20",
        job_active_state=JobActiveState.OPEN,
        likely_still_hiring=HiringLikelihood.LIKELY,
        first_seen_at=datetime(2026, 7, 27, 10, 0, 0),
        last_collected_at=datetime(2026, 7, 27, 11, 30, 0),
        job_status=JobStatus.ACTIVE,
        # 嵌套：年龄判定（exact_65_cap → eligible）
        age_result=AgeResult(
            candidate_age=60,
            age_evidence_raw="60岁以下",
            age_min=18,
            age_max=65,
            is_exact_65_cap=True,
            age_target_category=AgeTargetCategory.EXACT_65_CAP,
            age_match_category=AgeMatchCategory.ELIGIBLE,
            accepts_candidate_age=True,
            age_match_reason="明确 65 岁以下，60 岁符合",
            age_rule_id="rule_exact_65",
            boundary_risk=BoundaryRisk.NONE,
            age_confidence=Confidence.HIGH,
            age_needs_review=False,
        ),
        # 嵌套：劳动强度判定
        physical_intensity=PhysicalIntensityResult(
            physical_intensity_category=PhysicalIntensityCategory.LOW,
            physical_intensity_score=25,
            physical_intensity_evidence="坐岗为主",
            sitting_allowed=True,
            prolonged_standing=False,
            patrol_required=True,
            walking_intensity=WalkingIntensity.LOW,
            stair_climbing_required=False,
            lifting_required=False,
            lifting_weight_text=None,
            garbage_transport_required=False,
            outdoor_work=False,
            high_temperature_exposure=False,
            work_area_text="门岗亭",
            shift_type=ShiftType.DAY,
            night_shift_required=False,
            working_hours_text="8小时/天",
            rest_schedule_text="双休",
            physical_needs_review=False,
        ),
        # 嵌套：招聘者信息
        recruiter=RecruiterInfo(
            recruiter_name="王经理",
            recruiter_title="HR",
            activity_raw="刚刚活跃",
            activity_category=ActivityCategory.ACTIVE_3D,
            active_within_3d=True,
        ),
        # 嵌套：采集元
        collection_meta=CollectionMeta(
            source_page="https://x/list?page=1",
            collected_at=datetime(2026, 7, 27, 11, 30, 0),
            parse_ok=True,
            missing_fields=[],
            error_reason=None,
            manual_reviewed=False,
            manual_review_note=None,
            visited_jobs=True,
            last_detail_visit_at=datetime(2026, 7, 27, 11, 25, 0),
            detail_content_hash="abc123",
            skip_reason=None,
            revisit_allowed_at=datetime(2026, 7, 28, 11, 25, 0),
            list_stage_passed=True,
            detail_visit_count=1,
        ),
        # 评分与优先级
        score=85.5,
        score_breakdown={"age_match": 30, "physical_intensity": 25, "distance": 15},
        priority_rank=1,
        recommended_bucket="优先推荐",
    )


# ==================== Repository 完整往返测试（P0.1 新增） ====================
class TestJobRepositoryFullRoundtrip:
    """完整字段往返测试：upsert → commit → get_by_id，逐项断言恢复结果。"""

    def test_full_job_roundtrip(self, tmp_db_path: Path):
        """upsert 包含所有嵌套对象与评分字段的完整 Job，再读取并逐项断言。"""
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        original = _make_full_job()
        repo.upsert(original)
        db.commit()

        got = repo.get_by_id("FULL-1")
        assert got is not None

        # ===== 基础字段 =====
        assert got.job_id == "FULL-1"
        assert got.job_url == "https://x/full"
        assert got.job_title == "高档小区保安"
        assert got.company_name == "某高端物业公司"
        assert got.salary_raw == "5000-7000元/月"
        assert got.salary_min == 5000
        assert got.salary_max == 7000
        assert got.salary_months == 12
        assert got.experience == "1-3年"
        assert got.degree == "初中"
        assert got.job_tags == ["包吃", "包住", "五险"]
        assert got.job_desc_full == "负责小区门岗登记、巡逻等工作"
        assert got.job_desc_summary == "门岗与巡逻"
        assert got.address_raw == "杭州市拱墅区建国北路"
        assert got.address_std == "浙江省杭州市拱墅区建国北路"
        assert got.district == "拱墅区"
        assert got.longitude == 120.1769
        assert got.latitude == 30.2761
        assert got.distance_m == 850.5
        assert got.within_3km is True
        assert got.publish_time_raw == "2026-07-20"
        assert got.job_active_state == JobActiveState.OPEN.value
        assert got.likely_still_hiring == HiringLikelihood.LIKELY.value
        assert got.first_seen_at == datetime(2026, 7, 27, 10, 0, 0)
        assert got.last_collected_at == datetime(2026, 7, 27, 11, 30, 0)
        assert got.job_status == JobStatus.ACTIVE.value

        # ===== 嵌套：AgeResult =====
        age = got.age_result
        assert age is not None
        assert age.candidate_age == 60
        assert age.age_evidence_raw == "60岁以下"
        assert age.age_min == 18
        assert age.age_max == 65
        assert age.is_exact_65_cap is True
        assert age.age_target_category == "exact_65_cap"
        assert age.age_match_category == "eligible"
        assert age.accepts_candidate_age is True
        assert age.age_match_reason == "明确 65 岁以下，60 岁符合"
        assert age.age_rule_id == "rule_exact_65"
        assert age.boundary_risk == "none"
        assert age.age_confidence == "high"
        assert age.age_needs_review is False

        # ===== 嵌套：PhysicalIntensityResult =====
        phy = got.physical_intensity
        assert phy is not None
        assert phy.physical_intensity_category == "low"
        assert phy.physical_intensity_score == 25
        assert phy.physical_intensity_evidence == "坐岗为主"
        assert phy.sitting_allowed is True
        assert phy.prolonged_standing is False
        assert phy.patrol_required is True
        assert phy.walking_intensity == "low"
        assert phy.stair_climbing_required is False
        assert phy.lifting_required is False
        assert phy.lifting_weight_text is None
        assert phy.garbage_transport_required is False
        assert phy.outdoor_work is False
        assert phy.high_temperature_exposure is False
        assert phy.work_area_text == "门岗亭"
        assert phy.shift_type == "day"
        assert phy.night_shift_required is False
        assert phy.working_hours_text == "8小时/天"
        assert phy.rest_schedule_text == "双休"
        assert phy.physical_needs_review is False

        # ===== 嵌套：RecruiterInfo =====
        rec = got.recruiter
        assert rec is not None
        assert rec.recruiter_name == "王经理"
        assert rec.recruiter_title == "HR"
        assert rec.activity_raw == "刚刚活跃"
        assert rec.activity_category == "active_3d"
        assert rec.active_within_3d is True

        # ===== 嵌套：CollectionMeta =====
        meta = got.collection_meta
        assert meta is not None
        assert meta.source_page == "https://x/list?page=1"
        assert meta.collected_at == datetime(2026, 7, 27, 11, 30, 0)
        assert meta.parse_ok is True
        assert meta.missing_fields == []
        assert meta.error_reason is None
        assert meta.manual_reviewed is False
        assert meta.manual_review_note is None
        assert meta.visited_jobs is True
        assert meta.last_detail_visit_at == datetime(2026, 7, 27, 11, 25, 0)
        assert meta.detail_content_hash == "abc123"
        assert meta.skip_reason is None
        assert meta.revisit_allowed_at == datetime(2026, 7, 28, 11, 25, 0)
        assert meta.list_stage_passed is True
        assert meta.detail_visit_count == 1

        # ===== 评分与优先级 =====
        assert got.score == 85.5
        assert got.score_breakdown == {"age_match": 30, "physical_intensity": 25, "distance": 15}
        assert got.priority_rank == 1
        assert got.recommended_bucket == "优先推荐"

        db.close()

    def test_full_job_roundtrip_by_url(self, tmp_db_path: Path):
        """按 URL 查询也能完整恢复所有字段。"""
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        repo.upsert(_make_full_job())
        db.commit()
        got = repo.get_by_url("https://x/full")
        assert got is not None
        assert got.job_id == "FULL-1"
        assert got.age_result is not None
        assert got.physical_intensity is not None
        assert got.recruiter is not None
        assert got.collection_meta is not None
        assert got.score == 85.5
        db.close()


# ==================== 可空嵌套对象测试（P0.1 新增） ====================
class TestNullableNestedObjects:
    """嵌套对象为 None 时，保存与读取后仍应为 None。"""

    def test_all_nested_none(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        job = _make_job("N1", "https://x/n1")  # 默认所有嵌套对象为 None
        assert job.age_result is None
        assert job.physical_intensity is None
        assert job.recruiter is None
        assert job.collection_meta is None
        repo.upsert(job)
        db.commit()
        got = repo.get_by_id("N1")
        assert got is not None
        assert got.age_result is None
        assert got.physical_intensity is None
        assert got.recruiter is None
        assert got.collection_meta is None
        db.close()

    def test_only_age_set(self, tmp_db_path: Path):
        """仅设置 age_result，其他嵌套对象为 None。"""
        from boss_tool.enums import (
            AgeMatchCategory,
            AgeTargetCategory,
            BoundaryRisk,
            Confidence,
        )
        from boss_tool.models.age import AgeResult

        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        job = _make_job("N2", "https://x/n2")
        job = job.model_copy(
            update={
                "age_result": AgeResult(
                    candidate_age=60,
                    age_evidence_raw="60岁以下",
                    is_exact_65_cap=True,
                    age_target_category=AgeTargetCategory.EXACT_65_CAP,
                    age_match_category=AgeMatchCategory.ELIGIBLE,
                    accepts_candidate_age=True,
                    age_match_reason="测试",
                    boundary_risk=BoundaryRisk.NONE,
                    age_confidence=Confidence.HIGH,
                    age_needs_review=False,
                )
            }
        )
        repo.upsert(job)
        db.commit()
        got = repo.get_by_id("N2")
        assert got is not None
        assert got.age_result is not None
        assert got.age_result.age_target_category == "exact_65_cap"
        assert got.physical_intensity is None
        assert got.recruiter is None
        assert got.collection_meta is None
        db.close()


# ==================== 迁移测试（P0.1 新增） ====================
class TestMigrations:
    def test_empty_db_creates_all_tables_and_indices(self, tmp_db_path: Path):
        """空数据库 initialize 后，所有表和索引存在。"""
        db = Database(tmp_db_path)
        db.initialize()

        # 检查所有表存在
        tables = {
            row["name"]
            for row in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "jobs" in tables
        assert "job_snapshots" in tables
        assert "collection_meta" in tables
        assert "run_logs" in tables
        assert "geocode_cache" in tables
        assert "schema_version" in tables

        # 检查所有索引存在
        indices = {
            row["name"]
            for row in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%' "
                "OR name = 'uq_jobs_url'"
            ).fetchall()
        }
        assert "idx_jobs_within3km" in indices
        assert "idx_jobs_status" in indices
        assert "idx_jobs_age_target" in indices
        assert "idx_jobs_exact65" in indices
        assert "idx_jobs_hiring" in indices
        assert "idx_jobs_intensity" in indices
        assert "idx_jobs_act_cat" in indices
        assert "idx_jobs_bucket" in indices
        assert "idx_jobs_visited" in indices
        assert "idx_jobs_skip" in indices
        assert "idx_jobs_revisit" in indices
        assert "idx_jobs_last_col" in indices
        assert "uq_jobs_url" in indices
        assert "idx_snapshots_job" in indices
        assert "idx_snapshots_at" in indices
        assert "idx_meta_job" in indices

        db.close()

    def test_schema_version_is_one(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        assert db.get_schema_version() == 1
        db.close()

    def test_repeat_initialize_no_data_loss(self, tmp_db_path: Path):
        """重复 initialize 不改变数据。"""
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        repo.upsert(_make_job("R1", "https://x/r1"))
        db.commit()
        db.close()

        # 再次 initialize
        db2 = Database(tmp_db_path)
        db2.initialize()
        repo2 = JobRepository(db2.connection)
        got = repo2.get_by_id("R1")
        assert got is not None
        assert got.job_url == "https://x/r1"
        db2.close()

    def test_migration_failure_rolls_back(self, tmp_db_path: Path):
        """模拟 migration 失败时事务回滚：schema_version 不写入，表不创建。"""
        from boss_tool.storage import database as db_module

        # 保存原始迁移表
        original_migrations = dict(db_module.MIGRATIONS)

        # 注册一个会失败的 v2 迁移
        def failing_migration(conn):
            raise RuntimeError("模拟迁移失败")

        db_module.MIGRATIONS[2] = failing_migration
        failed_db = Database(tmp_db_path)
        try:
            with pytest.raises(RuntimeError, match="模拟迁移失败"):
                failed_db.initialize()  # 应当先成功应用 v1，然后 v2 失败
        finally:
            # 恢复原始迁移表
            db_module.MIGRATIONS.clear()
            db_module.MIGRATIONS.update(original_migrations)
            failed_db.close()

        # 验证 v1 成功应用（schema_version=1），v2 未写入
        db = Database(tmp_db_path)
        conn = db.connect()
        row = conn.execute("SELECT version FROM schema_version WHERE version=2").fetchone()
        assert row is None, "v2 不应写入 schema_version"
        row = conn.execute("SELECT version FROM schema_version WHERE version=1").fetchone()
        assert row is not None, "v1 应已写入 schema_version"
        db.close()

    def test_already_applied_migration_not_rerun(self, tmp_db_path: Path):
        """已应用的版本不重复运行。"""
        db = Database(tmp_db_path)
        db.initialize()
        # 应用一次后 schema_version 应为 1
        assert db.get_schema_version() == 1
        # 再次 initialize 不应重复执行 migration_v1_initial
        # 验证方式：在迁移函数上加 spy
        from boss_tool.storage import database as db_module

        call_count = {"count": 0}
        original_v1 = db_module.MIGRATIONS[1]

        def spy_v1(conn):
            call_count["count"] += 1
            original_v1(conn)

        db_module.MIGRATIONS[1] = spy_v1
        try:
            db.initialize()  # 第二次 initialize
            # migration_v1_initial 不应再次执行
            assert call_count["count"] == 0
        finally:
            db_module.MIGRATIONS[1] = original_v1
        db.close()


# ==================== CHECK 约束测试（P0.1 新增） ====================
class TestCheckConstraints:
    """验证 SQLite CHECK 约束能拒绝非法值。"""

    def test_negative_detail_visit_count_rejected(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute(
                "INSERT INTO jobs (job_id, job_url, job_title, company_name, "
                "first_seen_at, last_collected_at, detail_visit_count) "
                "VALUES ('J1', 'u1', 't', 'c', '2026-01-01', '2026-01-01', -1)"
            )
        db.close()

    def test_invalid_bool_value_rejected(self, tmp_db_path: Path):
        """visited_jobs 只允许 0/1。"""
        db = Database(tmp_db_path)
        db.initialize()
        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute(
                "INSERT INTO jobs (job_id, job_url, job_title, company_name, "
                "first_seen_at, last_collected_at, visited_jobs) "
                "VALUES ('J1', 'u1', 't', 'c', '2026-01-01', '2026-01-01', 5)"
            )
        db.close()

    def test_score_out_of_range_rejected(self, tmp_db_path: Path):
        """physical_intensity_score 必须在 0-100。"""
        db = Database(tmp_db_path)
        db.initialize()
        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute(
                "INSERT INTO jobs (job_id, job_url, job_title, company_name, "
                "first_seen_at, last_collected_at, physical_intensity_score) "
                "VALUES ('J1', 'u1', 't', 'c', '2026-01-01', '2026-01-01', 200)"
            )
        db.close()

    def test_negative_run_duration_rejected(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute(
                "INSERT INTO run_logs (run_id, started_at, status, run_duration_seconds) "
                "VALUES ('r1', '2026-01-01', 'completed', -1)"
            )
        db.close()
