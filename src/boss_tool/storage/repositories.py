"""基础 Repository。

P0 阶段仅提供：
- JobRepository.upsert()
- JobRepository.get_by_id()
- JobRepository.get_by_url()
- RunLogRepository.create()
- RunLogRepository.finish()

不实现采集工作流。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from boss_tool.logging_config import get_logger
from boss_tool.models.collection import CollectionMeta
from boss_tool.models.job import Job
from boss_tool.models.run import RunRecord

logger = get_logger(__name__)


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _from_iso(s: str | None) -> datetime | None:
    if s is None or s == "":
        return None
    return datetime.fromisoformat(s)


def _bool_to_int(v: bool | None) -> int | None:
    if v is None:
        return None
    return 1 if v else 0


def _int_to_bool(v: int | None) -> bool | None:
    if v is None:
        return None
    return bool(v)


def _json_dumps(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def _json_loads(s: str | None) -> Any:
    if s is None or s == "":
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


# ==================== JobRepository ====================
class JobRepository:
    """岗位 Repository。"""

    UPSERT_SQL = """
    INSERT INTO jobs (
        job_id, job_url, job_title, company_name,
        salary_raw, salary_min, salary_max, salary_unit, salary_months,
        experience, degree, job_tags,
        job_desc_full, job_desc_summary,
        address_raw, address_std, district,
        longitude, latitude, distance_m, within_3km,
        publish_time_raw, job_active_state, likely_still_hiring,
        first_seen_at, last_collected_at, job_status
    ) VALUES (
        :job_id, :job_url, :job_title, :company_name,
        :salary_raw, :salary_min, :salary_max, :salary_unit, :salary_months,
        :experience, :degree, :job_tags,
        :job_desc_full, :job_desc_summary,
        :address_raw, :address_std, :district,
        :longitude, :latitude, :distance_m, :within_3km,
        :publish_time_raw, :job_active_state, :likely_still_hiring,
        :first_seen_at, :last_collected_at, :job_status
    )
    ON CONFLICT(job_id) DO UPDATE SET
        job_url             = excluded.job_url,
        job_title           = excluded.job_title,
        company_name        = excluded.company_name,
        salary_raw          = excluded.salary_raw,
        salary_min          = excluded.salary_min,
        salary_max          = excluded.salary_max,
        salary_unit         = excluded.salary_unit,
        salary_months       = excluded.salary_months,
        experience          = excluded.experience,
        degree              = excluded.degree,
        job_tags            = excluded.job_tags,
        job_desc_full       = excluded.job_desc_full,
        job_desc_summary    = excluded.job_desc_summary,
        address_raw         = excluded.address_raw,
        address_std         = excluded.address_std,
        district            = excluded.district,
        longitude           = excluded.longitude,
        latitude            = excluded.latitude,
        distance_m          = excluded.distance_m,
        within_3km          = excluded.within_3km,
        publish_time_raw    = excluded.publish_time_raw,
        job_active_state    = excluded.job_active_state,
        likely_still_hiring = excluded.likely_still_hiring,
        last_collected_at   = excluded.last_collected_at,
        job_status          = 'updated'
    ;
    """

    SELECT_BY_ID_SQL = "SELECT * FROM jobs WHERE job_id = ?"
    SELECT_BY_URL_SQL = "SELECT * FROM jobs WHERE job_url = ?"

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(self, job: Job) -> Job:
        """插入或更新岗位。

        job_id 冲突时更新所有非主键字段，并将 job_status 标为 'updated'。
        job_url 唯一约束由索引 uq_jobs_url 保证。
        """
        params = {
            "job_id": job.job_id,
            "job_url": job.job_url,
            "job_title": job.job_title,
            "company_name": job.company_name,
            "salary_raw": job.salary_raw,
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            "salary_unit": job.salary_unit,
            "salary_months": job.salary_months,
            "experience": job.experience,
            "degree": job.degree,
            "job_tags": _json_dumps(job.job_tags),
            "job_desc_full": job.job_desc_full,
            "job_desc_summary": job.job_desc_summary,
            "address_raw": job.address_raw,
            "address_std": job.address_std,
            "district": job.district,
            "longitude": job.longitude,
            "latitude": job.latitude,
            "distance_m": job.distance_m,
            "within_3km": _bool_to_int(job.within_3km),
            "publish_time_raw": job.publish_time_raw,
            "job_active_state": job.job_active_state,
            "likely_still_hiring": job.likely_still_hiring,
            "first_seen_at": _to_iso(job.first_seen_at),
            "last_collected_at": _to_iso(job.last_collected_at),
            "job_status": job.job_status,
        }
        self.conn.execute(self.UPSERT_SQL, params)
        # 不在此处自动 commit；调用方负责通过 Database.transaction() 或显式 conn.commit() 提交
        # 这样在 transaction context 内可以正确回滚
        logger.debug("upsert job: %s", job.job_id)
        return job

    def get_by_id(self, job_id: str) -> Job | None:
        row = self.conn.execute(self.SELECT_BY_ID_SQL, (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def get_by_url(self, job_url: str) -> Job | None:
        row = self.conn.execute(self.SELECT_BY_URL_SQL, (job_url,)).fetchone()
        return self._row_to_job(row) if row else None

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        return Job(
            job_id=row["job_id"],
            job_url=row["job_url"],
            job_title=row["job_title"],
            company_name=row["company_name"],
            salary_raw=row["salary_raw"],
            salary_min=row["salary_min"],
            salary_max=row["salary_max"],
            salary_unit=row["salary_unit"],
            salary_months=row["salary_months"],
            experience=row["experience"],
            degree=row["degree"],
            job_tags=_json_loads(row["job_tags"]) or [],
            job_desc_full=row["job_desc_full"],
            job_desc_summary=row["job_desc_summary"],
            address_raw=row["address_raw"],
            address_std=row["address_std"],
            district=row["district"],
            longitude=row["longitude"],
            latitude=row["latitude"],
            distance_m=row["distance_m"],
            within_3km=_int_to_bool(row["within_3km"]),
            publish_time_raw=row["publish_time_raw"],
            job_active_state=row["job_active_state"],
            likely_still_hiring=row["likely_still_hiring"],
            first_seen_at=_from_iso(row["first_seen_at"]),  # type: ignore[arg-type]
            last_collected_at=_from_iso(row["last_collected_at"]),  # type: ignore[arg-type]
            job_status=row["job_status"],
        )


# ==================== CollectionMetaRepository ====================
class CollectionMetaRepository:
    """采集元 Repository（P0 仅基础持久化）。"""

    UPSERT_SQL = """
    INSERT INTO collection_meta (
        job_id, source_page, collected_at, parse_ok,
        missing_fields, error_reason,
        manual_reviewed, manual_review_note,
        visited_jobs, last_detail_visit_at,
        detail_content_hash, skip_reason,
        revisit_allowed_at, list_stage_passed, detail_visit_count
    ) VALUES (
        :job_id, :source_page, :collected_at, :parse_ok,
        :missing_fields, :error_reason,
        :manual_reviewed, :manual_review_note,
        :visited_jobs, :last_detail_visit_at,
        :detail_content_hash, :skip_reason,
        :revisit_allowed_at, :list_stage_passed, :detail_visit_count
    )
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, job_id: str, meta: CollectionMeta) -> int:
        params = {
            "job_id": job_id,
            "source_page": meta.source_page,
            "collected_at": _to_iso(meta.collected_at),
            "parse_ok": _bool_to_int(meta.parse_ok),
            "missing_fields": _json_dumps(meta.missing_fields),
            "error_reason": meta.error_reason,
            "manual_reviewed": _bool_to_int(meta.manual_reviewed),
            "manual_review_note": meta.manual_review_note,
            "visited_jobs": _bool_to_int(meta.visited_jobs),
            "last_detail_visit_at": _to_iso(meta.last_detail_visit_at),
            "detail_content_hash": meta.detail_content_hash,
            "skip_reason": meta.skip_reason,
            "revisit_allowed_at": _to_iso(meta.revisit_allowed_at),
            "list_stage_passed": _bool_to_int(meta.list_stage_passed),
            "detail_visit_count": meta.detail_visit_count,
        }
        cur = self.conn.execute(self.UPSERT_SQL, params)
        meta_id = int(cur.lastrowid) if cur.lastrowid else 0
        logger.debug("create collection_meta: job_id=%s meta_id=%s", job_id, meta_id)
        return meta_id


# ==================== RunLogRepository ====================
class RunLogRepository:
    """运行日志 Repository。"""

    CREATE_SQL = """
    INSERT INTO run_logs (
        run_id, started_at, status,
        account_warning_detected,
        page_count, detail_page_count, search_page_count,
        cache_hit_count, duplicate_skip_count, list_filter_skip_count,
        consecutive_errors,
        stopped_by_safety_rule, user_aborted,
        run_completed
    ) VALUES (
        :run_id, :started_at, :status,
        :account_warning_detected,
        :page_count, :detail_page_count, :search_page_count,
        :cache_hit_count, :duplicate_skip_count, :list_filter_skip_count,
        :consecutive_errors,
        :stopped_by_safety_rule, :user_aborted,
        :run_completed
    )
    """

    FINISH_SQL = """
    UPDATE run_logs SET
        ended_at = :ended_at,
        status = :status,
        stop_reason = :stop_reason,
        account_warning_detected = :account_warning_detected,
        warning_type = :warning_type,
        warning_text = :warning_text,
        page_count = :page_count,
        detail_page_count = :detail_page_count,
        search_page_count = :search_page_count,
        cache_hit_count = :cache_hit_count,
        duplicate_skip_count = :duplicate_skip_count,
        list_filter_skip_count = :list_filter_skip_count,
        run_duration_seconds = :run_duration_seconds,
        consecutive_errors = :consecutive_errors,
        stopped_by_safety_rule = :stopped_by_safety_rule,
        user_aborted = :user_aborted,
        last_successful_url = :last_successful_url,
        run_completed = :run_completed
    WHERE run_id = :run_id
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, record: RunRecord) -> None:
        """创建一条运行记录（仅写入 started_at 时刻字段）。"""
        params = {
            "run_id": record.run_id,
            "started_at": _to_iso(record.started_at),
            "status": record.status,
            "account_warning_detected": _bool_to_int(record.account_warning_detected),
            "page_count": record.page_count,
            "detail_page_count": record.detail_page_count,
            "search_page_count": record.search_page_count,
            "cache_hit_count": record.cache_hit_count,
            "duplicate_skip_count": record.duplicate_skip_count,
            "list_filter_skip_count": record.list_filter_skip_count,
            "consecutive_errors": record.consecutive_errors,
            "stopped_by_safety_rule": _bool_to_int(record.stopped_by_safety_rule),
            "user_aborted": _bool_to_int(record.user_aborted),
            "run_completed": _bool_to_int(False),
        }
        self.conn.execute(self.CREATE_SQL, params)
        logger.info("run_log created: run_id=%s", record.run_id)

    def finish(self, record: RunRecord) -> None:
        """更新一条运行记录为结束状态。"""
        params = {
            "run_id": record.run_id,
            "ended_at": _to_iso(record.ended_at),
            "status": record.status,
            "stop_reason": record.stop_reason,
            "account_warning_detected": _bool_to_int(record.account_warning_detected),
            "warning_type": record.warning_type,
            "warning_text": record.warning_text,
            "page_count": record.page_count,
            "detail_page_count": record.detail_page_count,
            "search_page_count": record.search_page_count,
            "cache_hit_count": record.cache_hit_count,
            "duplicate_skip_count": record.duplicate_skip_count,
            "list_filter_skip_count": record.list_filter_skip_count,
            "run_duration_seconds": record.run_duration_seconds,
            "consecutive_errors": record.consecutive_errors,
            "stopped_by_safety_rule": _bool_to_int(record.stopped_by_safety_rule),
            "user_aborted": _bool_to_int(record.user_aborted),
            "last_successful_url": record.last_successful_url,
            "run_completed": _bool_to_int(record.status == "completed"),
        }
        self.conn.execute(self.FINISH_SQL, params)
        logger.info(
            "run_log finished: run_id=%s status=%s stop_reason=%s",
            record.run_id,
            record.status,
            record.stop_reason,
        )


# ==================== GeocodeCacheRepository ====================
class GeocodeCacheRepository:
    """地理编码缓存 Repository。"""

    UPSERT_SQL = """
    INSERT INTO geocode_cache (query_text, standardized, longitude, latitude, district, fetched_at)
    VALUES (:query_text, :standardized, :longitude, :latitude, :district, :fetched_at)
    ON CONFLICT(query_text) DO UPDATE SET
        standardized = excluded.standardized,
        longitude    = excluded.longitude,
        latitude     = excluded.latitude,
        district     = excluded.district,
        fetched_at   = excluded.fetched_at
    """

    SELECT_SQL = "SELECT * FROM geocode_cache WHERE query_text = ?"

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(
        self,
        query_text: str,
        standardized: str | None,
        longitude: float | None,
        latitude: float | None,
        district: str | None,
        fetched_at: datetime,
    ) -> None:
        self.conn.execute(
            self.UPSERT_SQL,
            {
                "query_text": query_text,
                "standardized": standardized,
                "longitude": longitude,
                "latitude": latitude,
                "district": district,
                "fetched_at": _to_iso(fetched_at),
            },
        )

    def get(self, query_text: str) -> sqlite3.Row | None:
        return self.conn.execute(self.SELECT_SQL, (query_text,)).fetchone()


__all__ = [
    "JobRepository",
    "CollectionMetaRepository",
    "RunLogRepository",
    "GeocodeCacheRepository",
]
