"""Job 模型：岗位主表 1 行 = 1 岗位当前态。

按设计稿 v0.3 第 5.1 节字段字典定义。
P0.1：补充嵌套对象与评分字段，与数据库表完整对齐。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from boss_tool.enums import (
    HiringLikelihood,
    JobActiveState,
    JobStatus,
    SalaryUnit,
)
from boss_tool.models.age import AgeResult
from boss_tool.models.collection import CollectionMeta
from boss_tool.models.physical import PhysicalIntensityResult
from boss_tool.models.recruiter import RecruiterInfo


class Job(BaseModel):
    """岗位主表模型（1 行 = 1 岗位当前态）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    # ===== 标识与基础信息 =====
    job_id: str = Field(..., min_length=1, description="岗位唯一标识")
    job_url: str = Field(..., min_length=1, description="岗位详情页链接，去重主键之一")
    job_title: str = Field(..., min_length=1, description="岗位名称原文")
    company_name: str = Field(..., min_length=1, description="公司名称原文")

    # ===== 薪资 =====
    salary_raw: str | None = Field(default=None, description="薪资原文")
    salary_min: int | None = Field(default=None, ge=0, description="薪资最低值（元/月）")
    salary_max: int | None = Field(default=None, ge=0, description="薪资最高值（元/月）")
    salary_unit: SalaryUnit | None = Field(default=None, description="薪资单位")
    salary_months: int | None = Field(default=None, ge=1, le=24, description="含月数")

    # ===== 经验学历标签 =====
    experience: str | None = Field(default=None, description="工作经验要求原文")
    degree: str | None = Field(default=None, description="学历要求原文")
    job_tags: list[str] = Field(default_factory=list, description="岗位标签")

    # ===== 描述 =====
    job_desc_full: str | None = Field(default=None, description="完整岗位描述原文")
    job_desc_summary: str | None = Field(default=None, description="摘要前 N 字")

    # ===== 地址 =====
    address_raw: str | None = Field(default=None, description="工作地址原文")
    address_std: str | None = Field(default=None, description="标准化地址")
    district: str | None = Field(default=None, description="行政区（标准化后）")
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    distance_m: float | None = Field(default=None, ge=0.0, description="与中心点直线距离（米）")
    within_3km: bool | None = Field(
        default=None, description="是否在 3 公里内；地址不确定时为 null"
    )

    # ===== 时间与状态 =====
    publish_time_raw: str | None = Field(default=None, description="发布时间原文")
    job_active_state: JobActiveState = Field(default=JobActiveState.UNKNOWN)
    likely_still_hiring: HiringLikelihood = Field(
        default=HiringLikelihood.UNCERTAIN,
        description="仍在招聘可信度（v0.3 新增）",
    )
    first_seen_at: datetime = Field(..., description="首次发现时间")
    last_collected_at: datetime = Field(..., description="最近采集时间")
    job_status: JobStatus = Field(default=JobStatus.UNKNOWN)

    # ===== 嵌套：年龄判定结果 =====
    age_result: AgeResult | None = Field(default=None, description="年龄判定结果")

    # ===== 嵌套：劳动强度判定结果 =====
    physical_intensity: PhysicalIntensityResult | None = Field(
        default=None, description="劳动强度判定结果"
    )

    # ===== 嵌套：招聘者信息 =====
    recruiter: RecruiterInfo | None = Field(default=None, description="招聘者公开信息")

    # ===== 嵌套：采集元信息 =====
    collection_meta: CollectionMeta | None = Field(default=None, description="采集元信息")

    # ===== 评分与优先级 =====
    score: float | None = Field(default=None, description="综合评分")
    score_breakdown: dict[str, float] | None = Field(
        default=None, description="评分明细（各维度得分）"
    )
    priority_rank: int | None = Field(default=None, ge=0, description="优先级排名")
    recommended_bucket: str | None = Field(default=None, description="推荐桶名")

    @field_validator("job_url")
    @classmethod
    def _validate_job_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("job_url 不能为空")
        return v.strip()

    @model_validator(mode="after")
    def _validate_salary_consistency(self) -> Job:
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError(
                f"salary_min ({self.salary_min}) 不能大于 salary_max ({self.salary_max})"
            )
        return self


__all__ = ["Job"]
