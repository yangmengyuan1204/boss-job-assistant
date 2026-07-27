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
        # 更新
        updated = _make_job("J1", "https://x/j1", salary_min=4000, salary_max=6000)
        repo.upsert(updated)
        got = repo.get_by_id("J1")
        assert got is not None
        assert got.salary_min == 4000
        assert got.salary_max == 6000
        # job_status 应为 updated
        assert got.job_status == JobStatus.UPDATED.value
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
        got = repo.get_by_url("https://x/j1")
        assert got is not None
        assert got.job_id == "J1"
        db.close()

    def test_job_url_unique(self, tmp_db_path: Path):
        db = Database(tmp_db_path)
        db.initialize()
        repo = JobRepository(db.connection)
        repo.upsert(_make_job("J1", "https://x/j1"))
        # 不同 job_id 但相同 url 应触发唯一约束错误
        with pytest.raises(sqlite3.IntegrityError):
            repo.upsert(_make_job("J2", "https://x/j1", job_title="另一岗位"))
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
