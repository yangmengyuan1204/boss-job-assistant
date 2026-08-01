"""基础 Repository。

P0.1 阶段：
- JobRepository.upsert() / get_by_id() / get_by_url() 完整持久化所有字段
- 嵌套对象（AgeResult / PhysicalIntensityResult / RecruiterInfo / CollectionMeta）
  通过扁平字段存入 jobs 表同一行
- job_url 冲突时复用数据库已有 job_id
- job_status 使用 excluded.job_status，不写死 'updated'
- first_seen_at 在更新时不被覆盖
- RunLogRepository.create() / finish()
- CollectionMetaRepository.create()
- GeocodeCacheRepository.upsert() / get()

不实现采集工作流。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from boss_tool.enums import (
    ActivityCategory,
)
from boss_tool.logging_config import get_logger
from boss_tool.models.age import AgeResult
from boss_tool.models.collection import CollectionMeta
from boss_tool.models.job import Job
from boss_tool.models.job_detail import DetailUpsertOutcome, JobDetailRecord
from boss_tool.models.job_list import BulkUpsertResult, JobListRecord, UpsertOutcome
from boss_tool.models.physical import PhysicalIntensityResult
from boss_tool.models.recruiter import RecruiterInfo
from boss_tool.models.run import RunRecord

logger = get_logger(__name__)


# ==================== 通用转换函数 ====================
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


def _enum_to_str(v: Any) -> str | None:
    """枚举转字符串。已为字符串时直接返回。"""
    if v is None:
        return None
    if hasattr(v, "value"):
        return v.value
    return str(v)


# ==================== JobRepository ====================
class JobRepository:
    """岗位 Repository。

    完整持久化 Job 模型所有字段，包括嵌套对象：
    - AgeResult → 扁平化到 jobs 表 age_* 字段
    - PhysicalIntensityResult → 扁平化到 jobs 表 physical_* 字段
    - RecruiterInfo → 扁平化到 jobs 表 recruiter_* / activity_* 字段
    - CollectionMeta → 扁平化到 jobs 表采集元与缓存去重字段
    """

    UPSERT_SQL = """
    INSERT INTO jobs (
        job_id, job_url, job_title, company_name,
        salary_raw, salary_min, salary_max, salary_unit, salary_months,
        experience, degree, job_tags,
        job_desc_full, job_desc_summary,
        address_raw, address_std, district,
        longitude, latitude, distance_m, within_3km,
        publish_time_raw, job_active_state, likely_still_hiring,
        first_seen_at, last_collected_at, job_status,
        -- 年龄判定字段
        is_exact_65_cap, age_target_category, age_match_category,
        accepts_candidate_age, age_match_reason, age_rule_id,
        boundary_risk, age_confidence, age_needs_review,
        age_evidence_raw, age_min, age_max,
        -- 劳动强度字段
        physical_intensity_category, physical_intensity_score, physical_intensity_evidence,
        sitting_allowed, prolonged_standing, patrol_required, walking_intensity,
        stair_climbing_required, lifting_required, lifting_weight_text,
        garbage_transport_required, outdoor_work, high_temperature_exposure,
        work_area_text, shift_type, night_shift_required,
        working_hours_text, rest_schedule_text, physical_needs_review,
        -- 招聘者字段
        recruiter_name, recruiter_title, activity_raw, activity_category, active_within_3d,
        -- 缓存与去重
        visited_jobs, last_detail_visit_at, detail_content_hash,
        skip_reason, revisit_allowed_at, list_stage_passed, detail_visit_count,
        -- 采集元
        source_page, parse_ok, missing_fields, error_reason,
        manual_reviewed, manual_review_note,
        -- 评分与优先级
        score, score_breakdown, priority_rank, recommended_bucket
    ) VALUES (
        :job_id, :job_url, :job_title, :company_name,
        :salary_raw, :salary_min, :salary_max, :salary_unit, :salary_months,
        :experience, :degree, :job_tags,
        :job_desc_full, :job_desc_summary,
        :address_raw, :address_std, :district,
        :longitude, :latitude, :distance_m, :within_3km,
        :publish_time_raw, :job_active_state, :likely_still_hiring,
        :first_seen_at, :last_collected_at, :job_status,
        :is_exact_65_cap, :age_target_category, :age_match_category,
        :accepts_candidate_age, :age_match_reason, :age_rule_id,
        :boundary_risk, :age_confidence, :age_needs_review,
        :age_evidence_raw, :age_min, :age_max,
        :physical_intensity_category, :physical_intensity_score, :physical_intensity_evidence,
        :sitting_allowed, :prolonged_standing, :patrol_required, :walking_intensity,
        :stair_climbing_required, :lifting_required, :lifting_weight_text,
        :garbage_transport_required, :outdoor_work, :high_temperature_exposure,
        :work_area_text, :shift_type, :night_shift_required,
        :working_hours_text, :rest_schedule_text, :physical_needs_review,
        :recruiter_name, :recruiter_title, :activity_raw, :activity_category, :active_within_3d,
        :visited_jobs, :last_detail_visit_at, :detail_content_hash,
        :skip_reason, :revisit_allowed_at, :list_stage_passed, :detail_visit_count,
        :source_page, :parse_ok, :missing_fields, :error_reason,
        :manual_reviewed, :manual_review_note,
        :score, :score_breakdown, :priority_rank, :recommended_bucket
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
        address_std        = excluded.address_std,
        district            = excluded.district,
        longitude           = excluded.longitude,
        latitude            = excluded.latitude,
        distance_m          = excluded.distance_m,
        within_3km          = excluded.within_3km,
        publish_time_raw    = excluded.publish_time_raw,
        job_active_state    = excluded.job_active_state,
        likely_still_hiring = excluded.likely_still_hiring,
        last_collected_at   = excluded.last_collected_at,
        job_status          = excluded.job_status,
        is_exact_65_cap             = excluded.is_exact_65_cap,
        age_target_category         = excluded.age_target_category,
        age_match_category          = excluded.age_match_category,
        accepts_candidate_age       = excluded.accepts_candidate_age,
        age_match_reason            = excluded.age_match_reason,
        age_rule_id                 = excluded.age_rule_id,
        boundary_risk               = excluded.boundary_risk,
        age_confidence              = excluded.age_confidence,
        age_needs_review            = excluded.age_needs_review,
        age_evidence_raw            = excluded.age_evidence_raw,
        age_min                     = excluded.age_min,
        age_max                     = excluded.age_max,
        physical_intensity_category = excluded.physical_intensity_category,
        physical_intensity_score    = excluded.physical_intensity_score,
        physical_intensity_evidence = excluded.physical_intensity_evidence,
        sitting_allowed             = excluded.sitting_allowed,
        prolonged_standing          = excluded.prolonged_standing,
        patrol_required             = excluded.patrol_required,
        walking_intensity           = excluded.walking_intensity,
        stair_climbing_required     = excluded.stair_climbing_required,
        lifting_required            = excluded.lifting_required,
        lifting_weight_text         = excluded.lifting_weight_text,
        garbage_transport_required  = excluded.garbage_transport_required,
        outdoor_work                = excluded.outdoor_work,
        high_temperature_exposure   = excluded.high_temperature_exposure,
        work_area_text              = excluded.work_area_text,
        shift_type                  = excluded.shift_type,
        night_shift_required        = excluded.night_shift_required,
        working_hours_text          = excluded.working_hours_text,
        rest_schedule_text          = excluded.rest_schedule_text,
        physical_needs_review       = excluded.physical_needs_review,
        recruiter_name              = excluded.recruiter_name,
        recruiter_title             = excluded.recruiter_title,
        activity_raw                = excluded.activity_raw,
        activity_category           = excluded.activity_category,
        active_within_3d            = excluded.active_within_3d,
        visited_jobs                = excluded.visited_jobs,
        last_detail_visit_at        = excluded.last_detail_visit_at,
        detail_content_hash         = excluded.detail_content_hash,
        skip_reason                 = excluded.skip_reason,
        revisit_allowed_at          = excluded.revisit_allowed_at,
        list_stage_passed           = excluded.list_stage_passed,
        detail_visit_count          = excluded.detail_visit_count,
        source_page                 = excluded.source_page,
        parse_ok                    = excluded.parse_ok,
        missing_fields              = excluded.missing_fields,
        error_reason                = excluded.error_reason,
        manual_reviewed             = excluded.manual_reviewed,
        manual_review_note          = excluded.manual_review_note,
        score                       = excluded.score,
        score_breakdown             = excluded.score_breakdown,
        priority_rank               = excluded.priority_rank,
        recommended_bucket          = excluded.recommended_bucket
    ;
    """

    SELECT_BY_ID_SQL = "SELECT * FROM jobs WHERE job_id = ?"
    SELECT_BY_URL_SQL = "SELECT * FROM jobs WHERE job_url = ?"

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(self, job: Job) -> Job:
        """插入或更新岗位（完整持久化所有字段）。

        URL 冲突策略：
        - upsert 前先按 job_url 查询
        - 如果 URL 已存在且 job_id 不同，使用数据库已有 job_id 作为同一岗位
        - 不创建重复岗位
        - 在代码注释和测试中说明

        job_status 策略：
        - ON CONFLICT 时使用 excluded.job_status（不写死 'updated'）
        - 由调用方决定 job_status 值

        first_seen_at 策略：
        - ON CONFLICT 时 NOT excluded.first_seen_at，保留原值
        - SQL 未在 UPDATE SET 中包含 first_seen_at，因此不会被覆盖

        不自动 commit；调用方通过 Database.transaction() 或显式 conn.commit() 提交。
        """
        # URL 冲突处理：如果 URL 已存在但 job_id 不同，复用数据库已有 job_id
        job_id_to_use = job.job_id
        existing = self.get_by_url(job.job_url)
        if existing is not None and existing.job_id != job.job_id:
            logger.info(
                "URL 冲突：job_url=%s 已存在 job_id=%s，将复用而非创建 job_id=%s",
                job.job_url,
                existing.job_id,
                job.job_id,
            )
            job_id_to_use = existing.job_id
            # 用复用的 job_id 替换原 job_id 后再 upsert
            job = job.model_copy(update={"job_id": job_id_to_use})

        params = self._job_to_params(job)
        self.conn.execute(self.UPSERT_SQL, params)
        logger.debug("upsert job: %s", job.job_id)
        return job

    def get_by_id(self, job_id: str) -> Job | None:
        row = self.conn.execute(self.SELECT_BY_ID_SQL, (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def get_by_url(self, job_url: str) -> Job | None:
        row = self.conn.execute(self.SELECT_BY_URL_SQL, (job_url,)).fetchone()
        return self._row_to_job(row) if row else None

    # ==================== 私有：参数构造 ====================
    def _job_to_params(self, job: Job) -> dict[str, Any]:
        """将 Job 模型转为 SQL 参数字典（含嵌套对象扁平化）。"""
        # 基础字段
        params: dict[str, Any] = {
            "job_id": job.job_id,
            "job_url": job.job_url,
            "job_title": job.job_title,
            "company_name": job.company_name,
            "salary_raw": job.salary_raw,
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            "salary_unit": _enum_to_str(job.salary_unit),
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
            "job_active_state": _enum_to_str(job.job_active_state),
            "likely_still_hiring": _enum_to_str(job.likely_still_hiring),
            "first_seen_at": _to_iso(job.first_seen_at),
            "last_collected_at": _to_iso(job.last_collected_at),
            "job_status": _enum_to_str(job.job_status),
        }

        # 年龄判定字段
        age = job.age_result
        if age is not None:
            params.update(
                {
                    "is_exact_65_cap": _bool_to_int(age.is_exact_65_cap),
                    "age_target_category": _enum_to_str(age.age_target_category),
                    "age_match_category": _enum_to_str(age.age_match_category),
                    "accepts_candidate_age": _bool_to_int(age.accepts_candidate_age),
                    "age_match_reason": age.age_match_reason,
                    "age_rule_id": age.age_rule_id,
                    "boundary_risk": _enum_to_str(age.boundary_risk),
                    "age_confidence": _enum_to_str(age.age_confidence),
                    "age_needs_review": _bool_to_int(age.age_needs_review),
                    "age_evidence_raw": age.age_evidence_raw,
                    "age_min": age.age_min,
                    "age_max": age.age_max,
                }
            )
        else:
            params.update(
                {
                    "is_exact_65_cap": 0,
                    "age_target_category": None,
                    "age_match_category": None,
                    "accepts_candidate_age": None,
                    "age_match_reason": None,
                    "age_rule_id": None,
                    "boundary_risk": None,
                    "age_confidence": None,
                    "age_needs_review": 0,
                    "age_evidence_raw": None,
                    "age_min": None,
                    "age_max": None,
                }
            )

        # 劳动强度字段
        phy = job.physical_intensity
        if phy is not None:
            params.update(
                {
                    "physical_intensity_category": _enum_to_str(phy.physical_intensity_category),
                    "physical_intensity_score": phy.physical_intensity_score,
                    "physical_intensity_evidence": phy.physical_intensity_evidence,
                    "sitting_allowed": _bool_to_int(phy.sitting_allowed),
                    "prolonged_standing": _bool_to_int(phy.prolonged_standing),
                    "patrol_required": _bool_to_int(phy.patrol_required),
                    "walking_intensity": _enum_to_str(phy.walking_intensity),
                    "stair_climbing_required": _bool_to_int(phy.stair_climbing_required),
                    "lifting_required": _bool_to_int(phy.lifting_required),
                    "lifting_weight_text": phy.lifting_weight_text,
                    "garbage_transport_required": _bool_to_int(phy.garbage_transport_required),
                    "outdoor_work": _bool_to_int(phy.outdoor_work),
                    "high_temperature_exposure": _bool_to_int(phy.high_temperature_exposure),
                    "work_area_text": phy.work_area_text,
                    "shift_type": _enum_to_str(phy.shift_type),
                    "night_shift_required": _bool_to_int(phy.night_shift_required),
                    "working_hours_text": phy.working_hours_text,
                    "rest_schedule_text": phy.rest_schedule_text,
                    "physical_needs_review": _bool_to_int(phy.physical_needs_review),
                }
            )
        else:
            params.update(
                {
                    "physical_intensity_category": None,
                    "physical_intensity_score": None,
                    "physical_intensity_evidence": None,
                    "sitting_allowed": None,
                    "prolonged_standing": None,
                    "patrol_required": None,
                    "walking_intensity": None,
                    "stair_climbing_required": None,
                    "lifting_required": None,
                    "lifting_weight_text": None,
                    "garbage_transport_required": None,
                    "outdoor_work": None,
                    "high_temperature_exposure": None,
                    "work_area_text": None,
                    "shift_type": None,
                    "night_shift_required": None,
                    "working_hours_text": None,
                    "rest_schedule_text": None,
                    "physical_needs_review": 0,
                }
            )

        # 招聘者字段
        rec = job.recruiter
        if rec is not None:
            params.update(
                {
                    "recruiter_name": rec.recruiter_name,
                    "recruiter_title": rec.recruiter_title,
                    "activity_raw": rec.activity_raw,
                    "activity_category": _enum_to_str(rec.activity_category),
                    "active_within_3d": _bool_to_int(rec.active_within_3d),
                }
            )
        else:
            params.update(
                {
                    "recruiter_name": None,
                    "recruiter_title": None,
                    "activity_raw": None,
                    "activity_category": None,
                    "active_within_3d": None,
                }
            )

        # 采集元与缓存去重字段
        meta = job.collection_meta
        if meta is not None:
            params.update(
                {
                    "source_page": meta.source_page,
                    "parse_ok": _bool_to_int(meta.parse_ok),
                    "missing_fields": _json_dumps(meta.missing_fields),
                    "error_reason": meta.error_reason,
                    "manual_reviewed": _bool_to_int(meta.manual_reviewed),
                    "manual_review_note": meta.manual_review_note,
                    "visited_jobs": _bool_to_int(meta.visited_jobs),
                    "last_detail_visit_at": _to_iso(meta.last_detail_visit_at),
                    "detail_content_hash": meta.detail_content_hash,
                    "skip_reason": _enum_to_str(meta.skip_reason),
                    "revisit_allowed_at": _to_iso(meta.revisit_allowed_at),
                    "list_stage_passed": _bool_to_int(meta.list_stage_passed),
                    "detail_visit_count": meta.detail_visit_count,
                }
            )
        else:
            params.update(
                {
                    "source_page": None,
                    "parse_ok": 1,
                    "missing_fields": None,
                    "error_reason": None,
                    "manual_reviewed": 0,
                    "manual_review_note": None,
                    "visited_jobs": 0,
                    "last_detail_visit_at": None,
                    "detail_content_hash": None,
                    "skip_reason": None,
                    "revisit_allowed_at": None,
                    "list_stage_passed": 0,
                    "detail_visit_count": 0,
                }
            )

        # 评分与优先级
        params.update(
            {
                "score": job.score,
                "score_breakdown": _json_dumps(job.score_breakdown),
                "priority_rank": job.priority_rank,
                "recommended_bucket": job.recommended_bucket,
            }
        )

        return params

    # ==================== 私有：行转对象 ====================
    def _row_to_job(self, row: sqlite3.Row) -> Job:
        """从数据库行恢复 Job 模型（含嵌套对象）。

        嵌套对象恢复规则：
        - 如果该嵌套对象所有关键字段均为空，则恢复为 None
        - 如果存在任一业务字段，则构造对应 Pydantic 模型
        - 不创建内容全部为空但看起来像有效结果的伪对象
        """
        # 恢复 AgeResult
        age_result = self._restore_age_result(row)

        # 恢复 PhysicalIntensityResult
        physical_intensity = self._restore_physical_intensity(row)

        # 恢复 RecruiterInfo
        recruiter = self._restore_recruiter(row)

        # 恢复 CollectionMeta
        collection_meta = self._restore_collection_meta(row)

        return Job(
            # 基础字段
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
            # 嵌套对象
            age_result=age_result,
            physical_intensity=physical_intensity,
            recruiter=recruiter,
            collection_meta=collection_meta,
            # 评分与优先级
            score=row["score"],
            score_breakdown=_json_loads(row["score_breakdown"]),
            priority_rank=row["priority_rank"],
            recommended_bucket=row["recommended_bucket"],
        )

    def _restore_age_result(self, row: sqlite3.Row) -> AgeResult | None:
        """从数据库行恢复 AgeResult。

        如果 age_target_category 为空，认为未判定，返回 None。
        age_target_category 是必填字段，若它为空则 AgeResult 无法构造。
        """
        if row["age_target_category"] is None:
            return None
        return AgeResult(
            candidate_age=60,  # 固定写入 config 的求职者年龄
            age_evidence_raw=row["age_evidence_raw"],
            age_min=row["age_min"],
            age_max=row["age_max"],
            is_exact_65_cap=bool(row["is_exact_65_cap"]),
            age_target_category=row["age_target_category"],
            age_match_category=row["age_match_category"],
            accepts_candidate_age=_int_to_bool(row["accepts_candidate_age"]),
            age_match_reason=row["age_match_reason"],
            age_rule_id=row["age_rule_id"],
            boundary_risk=row["boundary_risk"],
            age_confidence=row["age_confidence"],
            age_needs_review=bool(row["age_needs_review"]),
        )

    def _restore_physical_intensity(self, row: sqlite3.Row) -> PhysicalIntensityResult | None:
        """从数据库行恢复 PhysicalIntensityResult。

        如果 physical_intensity_category 为空，认为未判定，返回 None。
        physical_intensity_category 是必填字段，若它为空则模型无法构造。
        """
        if row["physical_intensity_category"] is None:
            return None
        return PhysicalIntensityResult(
            physical_intensity_category=row["physical_intensity_category"],
            physical_intensity_score=row["physical_intensity_score"],
            physical_intensity_evidence=row["physical_intensity_evidence"],
            sitting_allowed=_int_to_bool(row["sitting_allowed"]),
            prolonged_standing=_int_to_bool(row["prolonged_standing"]),
            patrol_required=_int_to_bool(row["patrol_required"]),
            walking_intensity=row["walking_intensity"],
            stair_climbing_required=_int_to_bool(row["stair_climbing_required"]),
            lifting_required=_int_to_bool(row["lifting_required"]),
            lifting_weight_text=row["lifting_weight_text"],
            garbage_transport_required=_int_to_bool(row["garbage_transport_required"]),
            outdoor_work=_int_to_bool(row["outdoor_work"]),
            high_temperature_exposure=_int_to_bool(row["high_temperature_exposure"]),
            work_area_text=row["work_area_text"],
            shift_type=row["shift_type"],
            night_shift_required=_int_to_bool(row["night_shift_required"]),
            working_hours_text=row["working_hours_text"],
            rest_schedule_text=row["rest_schedule_text"],
            physical_needs_review=bool(row["physical_needs_review"]),
        )

    def _restore_recruiter(self, row: sqlite3.Row) -> RecruiterInfo | None:
        """从数据库行恢复 RecruiterInfo。

        如果所有 recruiter_* 与 activity_* 字段均为空，返回 None。
        activity_category 有默认值 'unknown'，因此只要任一字段有值就构造对象。
        """
        if (
            row["recruiter_name"] is None
            and row["recruiter_title"] is None
            and row["activity_raw"] is None
            and row["active_within_3d"] is None
        ):
            return None
        return RecruiterInfo(
            recruiter_name=row["recruiter_name"],
            recruiter_title=row["recruiter_title"],
            activity_raw=row["activity_raw"],
            activity_category=row["activity_category"] or ActivityCategory.UNKNOWN.value,
            active_within_3d=_int_to_bool(row["active_within_3d"]),
        )

    def _restore_collection_meta(self, row: sqlite3.Row) -> CollectionMeta | None:
        """从数据库行恢复 CollectionMeta。

        如果 source_page 为空，认为未采集，返回 None。
        source_page 是必填字段，若它为空则 CollectionMeta 无法构造。
        """
        if row["source_page"] is None:
            return None
        return CollectionMeta(
            source_page=row["source_page"],
            collected_at=_from_iso(row["last_collected_at"]),  # type: ignore[arg-type]
            parse_ok=bool(row["parse_ok"]),
            missing_fields=_json_loads(row["missing_fields"]) or [],
            error_reason=row["error_reason"],
            manual_reviewed=bool(row["manual_reviewed"]),
            manual_review_note=row["manual_review_note"],
            visited_jobs=bool(row["visited_jobs"]),
            last_detail_visit_at=_from_iso(row["last_detail_visit_at"]),
            detail_content_hash=row["detail_content_hash"],
            skip_reason=row["skip_reason"],
            revisit_allowed_at=_from_iso(row["revisit_allowed_at"]),
            list_stage_passed=bool(row["list_stage_passed"]),
            detail_visit_count=row["detail_visit_count"],
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
            "skip_reason": _enum_to_str(meta.skip_reason),
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
            "status": _enum_to_str(record.status),
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
        """更新一条运行记录为结束状态。

        run_completed 仅在 status 为 completed 时为 True。
        """
        # status 在 use_enum_values=True 时已是字符串
        status_str = record.status.value if hasattr(record.status, "value") else record.status
        params = {
            "run_id": record.run_id,
            "ended_at": _to_iso(record.ended_at),
            "status": _enum_to_str(record.status),
            "stop_reason": _enum_to_str(record.stop_reason),
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
            "run_completed": _bool_to_int(status_str == "completed"),
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


# ==================== JobListRepository ====================
class JobListRepository:
    """P3 搜索结果列表页采集 Repository。

    将 JobListRecord UPSERT 到 job_list 表。
    与 JobRepository 解耦：job_list 仅存储列表页公开可见字段，
    不涉及详情页/年龄判断/劳动强度/评分等后续阶段字段。

    去重策略：基于 job_id（UNIQUE 约束），使用 INSERT ... ON CONFLICT DO UPDATE。
    job_id 由 derive_job_id() 推导（URL 路径末段或 title+company+salary 哈希）。

    P3.1 三态 UPSERT：
    - NEW: 数据库中不存在该 job_id
    - UPDATED: 已存在且业务字段（title/salary/company/location/experience/
      education/job_url/company_url）任一变化
    - UNCHANGED: 已存在且业务字段全部相同（仅 collected_at / page_no 可能变化）
    - UNCHANGED 时仍更新 collected_at 与 page_no（采集元数据），但统计为重复
    - 同一批次内相同 job_id：第二条以第一条写入后的行为基线比较
      （顺序处理，统计总数 == 输入记录数，不会超过输入）

    P3.2 变更语义修正：
    - page_no 从 BUSINESS_FIELDS 移除，视为采集元数据（与 collected_at 同级）
      原因：同一岗位可能因排序变化从第 1 页移到第 2 页，内容无变化，不应 UPDATED
    """

    # 业务字段：用于判断 NEW / UPDATED / UNCHANGED
    # collected_at 与 page_no 均不参与变化判断（采集元数据）
    BUSINESS_FIELDS = (
        "title",
        "salary",
        "company",
        "location",
        "experience",
        "education",
        "job_url",
        "company_url",
    )

    UPSERT_SQL = """
    INSERT INTO job_list (
        job_id, title, salary, company, location,
        experience, education, job_url, company_url,
        page_no, collected_at
    ) VALUES (
        :job_id, :title, :salary, :company, :location,
        :experience, :education, :job_url, :company_url,
        :page_no, :collected_at
    )
    ON CONFLICT(job_id) DO UPDATE SET
        title       = excluded.title,
        salary      = excluded.salary,
        company     = excluded.company,
        location    = excluded.location,
        experience  = excluded.experience,
        education   = excluded.education,
        job_url     = excluded.job_url,
        company_url = excluded.company_url,
        page_no     = excluded.page_no,
        collected_at = excluded.collected_at
    """

    COUNT_SQL = "SELECT COUNT(*) AS cnt FROM job_list"

    COUNT_BY_JOB_ID_SQL = "SELECT COUNT(*) AS cnt FROM job_list WHERE job_id = ?"

    SELECT_BY_JOB_ID_SQL = """
    SELECT job_id, title, salary, company, location,
           experience, education, job_url, company_url,
           page_no, collected_at
    FROM job_list
    WHERE job_id = ?
    """

    SELECT_ALL_SQL = """
    SELECT job_id, title, salary, company, location,
           experience, education, job_url, company_url,
           page_no, collected_at
    FROM job_list
    ORDER BY id
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_by_job_id(self, job_id: str) -> sqlite3.Row | None:
        """按 job_id 查询现有记录。不存在返回 None。"""
        return self.conn.execute(self.SELECT_BY_JOB_ID_SQL, (job_id,)).fetchone()

    @staticmethod
    def _business_fields_equal(record: JobListRecord, existing: sqlite3.Row) -> bool:
        """比较 record 与现有行的业务字段是否全部相同。

        collected_at 不参与比较（不单独导致 UPDATED）。
        None 与 NULL 视为相同（数据库 NULL 与 Python None）。
        空字符串与 NULL 视为相同（防御性规范化）。
        """
        for field in JobListRepository.BUSINESS_FIELDS:
            new_val = getattr(record, field)
            old_val = existing[field] if field in existing.keys() else None
            # 规范化：None / "" 统一为 None 比较
            new_norm = new_val if new_val not in (None, "") else None
            old_norm = old_val if old_val not in (None, "") else None
            if new_norm != old_norm:
                return False
        return True

    def save_job_list(self, record: JobListRecord) -> UpsertOutcome:
        """保存单条 JobListRecord（三态 UPSERT）。

        Args:
            record: 岗位列表记录

        Returns:
            UpsertOutcome.NEW / UPDATED / UNCHANGED
            - NEW: 数据库中不存在该 job_id
            - UPDATED: 已存在且业务字段变化
            - UNCHANGED: 已存在且业务字段全部相同（collected_at 仍被更新）
        """
        existing = self.get_by_job_id(record.job_id)

        if existing is None:
            outcome = UpsertOutcome.NEW
        elif self._business_fields_equal(record, existing):
            outcome = UpsertOutcome.UNCHANGED
        else:
            outcome = UpsertOutcome.UPDATED

        self.conn.execute(
            self.UPSERT_SQL,
            {
                "job_id": record.job_id,
                "title": record.title,
                "salary": record.salary,
                "company": record.company,
                "location": record.location,
                "experience": record.experience,
                "education": record.education,
                "job_url": record.job_url,
                "company_url": record.company_url,
                "page_no": record.page_no,
                "collected_at": _to_iso(record.collected_at),
            },
        )
        return outcome

    def bulk_upsert_job_list(self, records: list[JobListRecord]) -> BulkUpsertResult:
        """批量 UPSERT JobListRecord。

        顺序处理：每条记录以之前写入后的数据库行为基线比较。
        同一批次内相同 job_id 不会导致统计超过输入记录数。

        Args:
            records: 岗位列表记录列表

        Returns:
            BulkUpsertResult(new_count, updated_count, unchanged_count)
            new + updated + unchanged == len(records)
        """
        result = BulkUpsertResult()
        for record in records:
            outcome = self.save_job_list(record)
            if outcome == UpsertOutcome.NEW:
                result.new_count += 1
            elif outcome == UpsertOutcome.UPDATED:
                result.updated_count += 1
            else:
                result.unchanged_count += 1
        return result

    def count(self) -> int:
        """返回 job_list 表总记录数。"""
        row = self.conn.execute(self.COUNT_SQL).fetchone()
        return int(row["cnt"])

    def exists(self, job_id: str) -> bool:
        """检查指定 job_id 是否已存在。"""
        row = self.conn.execute(self.COUNT_BY_JOB_ID_SQL, (job_id,)).fetchone()
        return int(row["cnt"]) > 0

    def get_all(self) -> list[sqlite3.Row]:
        """返回所有记录（按插入顺序）。"""
        return self.conn.execute(self.SELECT_ALL_SQL).fetchall()


# ==================== JobDetailRepository ====================
class JobDetailRepository:
    """P4 岗位详情页采集 Repository。

    将 JobDetailRecord UPSERT 到 job_detail 表。
    与 JobListRepository 解耦：job_detail 仅存储详情页公开可见字段，
    不涉及年龄判断/劳动强度/评分等后续阶段字段。

    去重策略：基于 job_id（UNIQUE 约束），使用 INSERT ... ON CONFLICT DO UPDATE。
    job_id 与 job_list 同源（复用 derive_job_id 推导规则）。

    P4 三态 UPSERT：
    - NEW: 数据库中不存在该 job_id
    - UPDATED: 已存在且业务字段任一变化
    - UNCHANGED: 已存在且业务字段全部相同（仅 collected_at 可能变化）
    - UNCHANGED 时仍更新 collected_at（采集元数据）
    - collected_at 单独变化不算 UPDATED

    列表/标签类字段比较前必须进行确定性规范化：
    - 去重（保持首次出现顺序）
    - JSON 序列化稳定（ensure_ascii=False, sort_keys=False，依赖上游去重）
    - 空列表与 NULL 视为相同（规范化为 NULL 比较）
    """

    # 业务字段：用于判断 NEW / UPDATED / UNCHANGED
    # collected_at 不参与（采集元数据）
    # description_truncated 不参与（属于解析诊断，不属于业务内容）
    BUSINESS_FIELDS = (
        "job_url",
        "title",
        "salary",
        "location",
        "experience",
        "education",
        "employment_type",
        "description",
        "company",
        "company_url",
        "company_industry",
        "company_size",
        "company_stage",
        "recruiter_name",
        "recruiter_title",
        "recruiter_active",
        "benefits_json",
        "tags_json",
    )

    UPSERT_SQL = """
    INSERT INTO job_detail (
        job_id, job_url, title, salary, location,
        experience, education, employment_type, description,
        company, company_url, company_industry, company_size, company_stage,
        recruiter_name, recruiter_title, recruiter_active,
        benefits_json, tags_json, collected_at
    ) VALUES (
        :job_id, :job_url, :title, :salary, :location,
        :experience, :education, :employment_type, :description,
        :company, :company_url, :company_industry, :company_size, :company_stage,
        :recruiter_name, :recruiter_title, :recruiter_active,
        :benefits_json, :tags_json, :collected_at
    )
    ON CONFLICT(job_id) DO UPDATE SET
        job_url           = excluded.job_url,
        title             = excluded.title,
        salary            = excluded.salary,
        location          = excluded.location,
        experience        = excluded.experience,
        education         = excluded.education,
        employment_type   = excluded.employment_type,
        description       = excluded.description,
        company           = excluded.company,
        company_url       = excluded.company_url,
        company_industry  = excluded.company_industry,
        company_size      = excluded.company_size,
        company_stage     = excluded.company_stage,
        recruiter_name    = excluded.recruiter_name,
        recruiter_title   = excluded.recruiter_title,
        recruiter_active  = excluded.recruiter_active,
        benefits_json     = excluded.benefits_json,
        tags_json         = excluded.tags_json,
        collected_at      = excluded.collected_at
    """

    COUNT_SQL = "SELECT COUNT(*) AS cnt FROM job_detail"

    COUNT_BY_JOB_ID_SQL = "SELECT COUNT(*) AS cnt FROM job_detail WHERE job_id = ?"

    SELECT_BY_JOB_ID_SQL = """
    SELECT job_id, job_url, title, salary, location,
           experience, education, employment_type, description,
           company, company_url, company_industry, company_size, company_stage,
           recruiter_name, recruiter_title, recruiter_active,
           benefits_json, tags_json, collected_at
    FROM job_detail
    WHERE job_id = ?
    """

    SELECT_ALL_SQL = """
    SELECT job_id, job_url, title, salary, location,
           experience, education, employment_type, description,
           company, company_url, company_industry, company_size, company_stage,
           recruiter_name, recruiter_title, recruiter_active,
           benefits_json, tags_json, collected_at
    FROM job_detail
    ORDER BY id
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_by_job_id(self, job_id: str) -> sqlite3.Row | None:
        """按 job_id 查询现有记录。不存在返回 None。"""
        return self.conn.execute(self.SELECT_BY_JOB_ID_SQL, (job_id,)).fetchone()

    @staticmethod
    def _normalize_scalar(v: str | None) -> str | None:
        """规范化标量字段：None / "" 统一为 None。"""
        if v is None or v == "":
            return None
        return v

    @staticmethod
    def _normalize_list_json(v: str | None) -> str | None:
        """规范化列表 JSON 字段：None / 空列表 / "[]" 统一为 None。

        数据库中以 JSON 字符串存储列表。比较前需确定性规范化：
        - None → None
        - "[]"（空列表 JSON）→ None
        - 非 JSON 字符串 → 原样返回（不应发生，防御性）
        - 非空 JSON 列表 → 原样返回（依赖上游去重保证确定性）
        """
        if v is None or v == "":
            return None
        if v == "[]":
            return None
        return v

    @classmethod
    def _business_fields_equal(cls, record: JobDetailRecord, existing: sqlite3.Row) -> bool:
        """比较 record 与现有行的业务字段是否全部相同。

        collected_at 不参与比较（不单独导致 UPDATED）。
        None 与 NULL 视为相同。
        空字符串与 NULL 视为相同（防御性规范化）。
        空列表 JSON 与 NULL 视为相同（防御性规范化）。
        """
        for field in cls.BUSINESS_FIELDS:
            if field in ("benefits_json", "tags_json"):
                # 列表字段：从 record 序列化为 JSON，与数据库中已有的 JSON 比较
                list_attr = field.replace("_json", "")
                new_val = _json_dumps(getattr(record, list_attr))
                old_val = existing[field] if field in existing.keys() else None
                if cls._normalize_list_json(new_val) != cls._normalize_list_json(old_val):
                    return False
            else:
                # 标量字段
                new_val = getattr(record, field)
                old_val = existing[field] if field in existing.keys() else None
                if cls._normalize_scalar(new_val) != cls._normalize_scalar(old_val):
                    return False
        return True

    def save_job_detail(self, record: JobDetailRecord) -> DetailUpsertOutcome:
        """保存单条 JobDetailRecord（三态 UPSERT）。

        Args:
            record: 岗位详情记录

        Returns:
            DetailUpsertOutcome.NEW / UPDATED / UNCHANGED
            - NEW: 数据库中不存在该 job_id
            - UPDATED: 已存在且业务字段变化
            - UNCHANGED: 已存在且业务字段全部相同（collected_at 仍被更新）
        """
        existing = self.get_by_job_id(record.job_id)

        if existing is None:
            outcome = DetailUpsertOutcome.NEW
        elif self._business_fields_equal(record, existing):
            outcome = DetailUpsertOutcome.UNCHANGED
        else:
            outcome = DetailUpsertOutcome.UPDATED

        self.conn.execute(
            self.UPSERT_SQL,
            {
                "job_id": record.job_id,
                "job_url": record.job_url,
                "title": record.title,
                "salary": record.salary,
                "location": record.location,
                "experience": record.experience,
                "education": record.education,
                "employment_type": record.employment_type,
                "description": record.description,
                "company": record.company,
                "company_url": record.company_url,
                "company_industry": record.company_industry,
                "company_size": record.company_size,
                "company_stage": record.company_stage,
                "recruiter_name": record.recruiter_name,
                "recruiter_title": record.recruiter_title,
                "recruiter_active": record.recruiter_active,
                "benefits_json": _json_dumps(record.benefits),
                "tags_json": _json_dumps(record.tags),
                "collected_at": _to_iso(record.collected_at),
            },
        )
        return outcome

    def count(self) -> int:
        """返回 job_detail 表总记录数。"""
        row = self.conn.execute(self.COUNT_SQL).fetchone()
        return int(row["cnt"])

    def exists(self, job_id: str) -> bool:
        """检查指定 job_id 是否已存在。"""
        row = self.conn.execute(self.COUNT_BY_JOB_ID_SQL, (job_id,)).fetchone()
        return int(row["cnt"]) > 0

    def get_all(self) -> list[sqlite3.Row]:
        """返回所有记录（按插入顺序）。"""
        return self.conn.execute(self.SELECT_ALL_SQL).fetchall()


__all__ = [
    "JobRepository",
    "CollectionMetaRepository",
    "RunLogRepository",
    "GeocodeCacheRepository",
    "JobListRepository",
    "JobDetailRepository",
]
