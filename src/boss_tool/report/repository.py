"""P7 只读查询层。

ReportRepository 负责：
- 只读 SELECT，不写 SQLite，不修改岗位记录
- 以 job_id 关联 job_list 和 job_detail（FULL OUTER via UNION of LEFT JOINs）
- 详情数据优先于列表数据（同名字段取 job_detail 值，缺则取 job_list）
- 缺少详情记录时仍允许展示列表记录（data_source = "list_only"）
- 缺少列表记录时仍允许展示详情记录（data_source = "detail"）
- 不得因一个字段缺失而丢弃整条岗位
- 保留数据来源标记：list_only / detail

距离陷阱处理：
- RuleResult.distance_meter 持久化时被丢弃
- 必须从 job_detail.distance_meter（V4 地理列）取
- COALESCE(d.distance_meter, l.distance_meter) 详情优先
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from boss_tool.logging_config import get_logger
from boss_tool.report.age_fit import compute_age_fit
from boss_tool.report.models import ReportJob

logger = get_logger(__name__)


# ==================== 查询 SQL ====================
# 完整聚合 job_list 与 job_detail：
# - LEFT JOIN job_list → job_detail：覆盖"有列表无详情"
# - LEFT JOIN job_detail → job_list：覆盖"有详情无列表"
# - UNION 去重（job_id 相同的记录由 COALESCE 合并）
# - 详情优先：同名字段 COALESCE(d.x, l.x)
# - 距离从 V4 地理列取，不从 RuleResult 取
FETCH_ALL_SQL = """
SELECT
    COALESCE(d.job_id, l.job_id) AS job_id,
    COALESCE(d.title, l.title) AS title,
    COALESCE(d.salary, l.salary) AS salary,
    COALESCE(d.company, l.company) AS company,
    COALESCE(d.location, l.location) AS location,
    COALESCE(d.experience, l.experience) AS experience,
    COALESCE(d.education, l.education) AS education,
    COALESCE(d.job_url, l.job_url) AS job_url,
    d.employment_type AS employment_type,
    d.description AS description,
    d.company_url AS company_url,
    d.company_industry AS company_industry,
    d.company_size AS company_size,
    d.company_stage AS company_stage,
    d.recruiter_name AS recruiter_name,
    d.recruiter_title AS recruiter_title,
    d.recruiter_active AS recruiter_active,
    d.benefits_json AS benefits_json,
    d.tags_json AS tags_json,
    COALESCE(d.normalized_address, l.normalized_address) AS normalized_address,
    COALESCE(d.longitude, l.longitude) AS longitude,
    COALESCE(d.latitude, l.latitude) AS latitude,
    COALESCE(d.distance_meter, l.distance_meter) AS distance_meter,
    COALESCE(d.within_3km, l.within_3km) AS within_3km,
    d.score AS score,
    d.recommend_level AS recommend_level,
    d.job_category AS job_category,
    d.age_requirement_text AS age_requirement_text,
    d.age_status AS age_status,
    d.recruiter_active_level AS recruiter_active_level,
    d.matched_rules_json AS matched_rules_json,
    d.failed_rules_json AS failed_rules_json,
    d.warnings_json AS warnings_json,
    d.explanations_json AS explanations_json,
    d.labor_intensity_tags_json AS labor_intensity_tags_json,
    d.score_breakdown_json AS score_breakdown_json,
    l.page_no AS page_no,
    COALESCE(d.collected_at, l.collected_at) AS collected_at,
    CASE WHEN d.job_id IS NOT NULL THEN 'detail' ELSE 'list_only' END AS data_source
FROM job_list l
LEFT JOIN job_detail d ON l.job_id = d.job_id
UNION
SELECT
    COALESCE(d2.job_id, l2.job_id) AS job_id,
    COALESCE(d2.title, l2.title) AS title,
    COALESCE(d2.salary, l2.salary) AS salary,
    COALESCE(d2.company, l2.company) AS company,
    COALESCE(d2.location, l2.location) AS location,
    COALESCE(d2.experience, l2.experience) AS experience,
    COALESCE(d2.education, l2.education) AS education,
    COALESCE(d2.job_url, l2.job_url) AS job_url,
    d2.employment_type AS employment_type,
    d2.description AS description,
    d2.company_url AS company_url,
    d2.company_industry AS company_industry,
    d2.company_size AS company_size,
    d2.company_stage AS company_stage,
    d2.recruiter_name AS recruiter_name,
    d2.recruiter_title AS recruiter_title,
    d2.recruiter_active AS recruiter_active,
    d2.benefits_json AS benefits_json,
    d2.tags_json AS tags_json,
    COALESCE(d2.normalized_address, l2.normalized_address) AS normalized_address,
    COALESCE(d2.longitude, l2.longitude) AS longitude,
    COALESCE(d2.latitude, l2.latitude) AS latitude,
    COALESCE(d2.distance_meter, l2.distance_meter) AS distance_meter,
    COALESCE(d2.within_3km, l2.within_3km) AS within_3km,
    d2.score AS score,
    d2.recommend_level AS recommend_level,
    d2.job_category AS job_category,
    d2.age_requirement_text AS age_requirement_text,
    d2.age_status AS age_status,
    d2.recruiter_active_level AS recruiter_active_level,
    d2.matched_rules_json AS matched_rules_json,
    d2.failed_rules_json AS failed_rules_json,
    d2.warnings_json AS warnings_json,
    d2.explanations_json AS explanations_json,
    d2.labor_intensity_tags_json AS labor_intensity_tags_json,
    d2.score_breakdown_json AS score_breakdown_json,
    l2.page_no AS page_no,
    COALESCE(d2.collected_at, l2.collected_at) AS collected_at,
    CASE WHEN d2.job_id IS NOT NULL THEN 'detail' ELSE 'list_only' END AS data_source
FROM job_detail d2
LEFT JOIN job_list l2 ON d2.job_id = l2.job_id
WHERE l2.job_id IS NULL
"""

COUNT_TOTAL_SQL = (
    "SELECT COUNT(*) AS cnt FROM (SELECT job_id FROM job_list UNION SELECT job_id FROM job_detail)"
)


class ReportRepository:
    """只读查询层。

    只读 SELECT，不写 SQLite，不修改岗位记录。
    以 job_id 关联 job_list 和 job_detail，详情数据优先。

    Usage:
        repo = ReportRepository(conn)
        jobs = repo.fetch_all_jobs()
        total = repo.count_total()
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def fetch_all_jobs(self) -> list[ReportJob]:
        """查询所有岗位（聚合 job_list + job_detail）。

        Returns:
            ReportJob 列表（未排序，由调用方排序）
        """
        rows = self.conn.execute(FETCH_ALL_SQL).fetchall()
        jobs: list[ReportJob] = []
        for row in rows:
            try:
                job = self._row_to_report_job(row)
                jobs.append(job)
            except Exception:
                # 单条解析失败不丢弃整批，记录日志后跳过
                job_id = row["job_id"] if "job_id" in row.keys() else "<unknown>"
                logger.exception("解析岗位行失败 job_id=%s，跳过", job_id)
        return jobs

    def count_total(self) -> int:
        """返回岗位总数（job_list 与 job_detail 的并集）。"""
        row = self.conn.execute(COUNT_TOTAL_SQL).fetchone()
        return int(row["cnt"])

    # ==================== 私有：行转对象 ====================
    @staticmethod
    def _row_to_report_job(row: sqlite3.Row) -> ReportJob:
        """从数据库行构造 ReportJob。

        - JSON 字段反序列化为列表/字典
        - 空值安全降级为 None / 空列表 / 空字典
        - 计算 60 岁适配状态（基于 age_status）
        - within_3km 从 0/1/NULL 转换为 bool/None
        """
        # 解析 JSON 字段
        benefits = _json_loads_list(row["benefits_json"])
        tags = _json_loads_list(row["tags_json"])
        matched_rules = _json_loads_list(row["matched_rules_json"])
        failed_rules = _json_loads_list(row["failed_rules_json"])
        warnings = _json_loads_list(row["warnings_json"])
        explanations = _json_loads_list(row["explanations_json"])
        labor_intensity_tags = _json_loads_list(row["labor_intensity_tags_json"])
        score_breakdown = _json_loads_dict(row["score_breakdown_json"])

        # within_3km 转换
        within_3km_raw = row["within_3km"]
        within_3km: bool | None = None if within_3km_raw is None else bool(within_3km_raw)

        # 距离转换
        distance_meter_raw = row["distance_meter"]
        distance_meter: float | None = (
            None if distance_meter_raw is None else float(distance_meter_raw)
        )

        # 评分转换
        score_raw = row["score"]
        score: int | None = None if score_raw is None else int(score_raw)

        # 经纬度转换
        longitude_raw = row["longitude"]
        longitude: float | None = float(longitude_raw) if longitude_raw is not None else None
        latitude_raw = row["latitude"]
        latitude: float | None = float(latitude_raw) if latitude_raw is not None else None

        # 采集时间转换
        collected_at_raw = row["collected_at"]
        collected_at: datetime | None
        if collected_at_raw is None or collected_at_raw == "":
            collected_at = None
        else:
            try:
                collected_at = datetime.fromisoformat(collected_at_raw)
            except (ValueError, TypeError):
                collected_at = None

        # 年龄适配计算
        age_status = row["age_status"]
        candidate_age_fit, candidate_age_fit_reason = compute_age_fit(age_status)

        # data_source 默认值
        data_source = row["data_source"] if row["data_source"] else "list_only"

        return ReportJob(
            job_id=row["job_id"],
            title=row["title"],
            salary=row["salary"],
            company=row["company"],
            location=row["location"],
            experience=row["experience"],
            education=row["education"],
            job_url=row["job_url"],
            employment_type=row["employment_type"],
            description=row["description"],
            company_url=row["company_url"],
            company_industry=row["company_industry"],
            company_size=row["company_size"],
            company_stage=row["company_stage"],
            recruiter_name=row["recruiter_name"],
            recruiter_title=row["recruiter_title"],
            recruiter_active=row["recruiter_active"],
            benefits=benefits,
            tags=tags,
            normalized_address=row["normalized_address"],
            longitude=longitude,
            latitude=latitude,
            distance_meter=distance_meter,
            within_3km=within_3km,
            score=score,
            recommend_level=row["recommend_level"],
            job_category=row["job_category"],
            age_requirement_text=row["age_requirement_text"],
            age_status=age_status,
            recruiter_active_level=row["recruiter_active_level"],
            matched_rules=matched_rules,
            failed_rules=failed_rules,
            warnings=warnings,
            explanations=explanations,
            labor_intensity_tags=labor_intensity_tags,
            score_breakdown=score_breakdown,
            candidate_age_fit=candidate_age_fit,
            candidate_age_fit_reason=candidate_age_fit_reason,
            page_no=row["page_no"],
            collected_at=collected_at,
            data_source=data_source,
        )


# ==================== 辅助函数 ====================
def _json_loads_list(s: Any) -> list[str]:
    """安全反序列化 JSON 列表字段。失败返回空列表。"""
    if s is None or s == "":
        return []
    if isinstance(s, list):
        return [str(x) for x in s]
    try:
        result = json.loads(s)
        if isinstance(result, list):
            return [str(x) for x in result]
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def _json_loads_dict(s: Any) -> dict[str, int]:
    """安全反序列化 JSON 字典字段。失败返回空字典。"""
    if s is None or s == "":
        return {}
    if isinstance(s, dict):
        return {str(k): int(v) for k, v in s.items() if isinstance(v, (int, float))}
    try:
        result = json.loads(s)
        if isinstance(result, dict):
            return {str(k): int(v) for k, v in result.items() if isinstance(v, (int, float))}
        return {}
    except (json.JSONDecodeError, TypeError):
        return {}


__all__ = ["ReportRepository"]
