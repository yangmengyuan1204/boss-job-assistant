"""PhysicalIntensityResult 模型：劳动强度判定结果。

按设计稿 v0.3 第 5.3 节与第八节定义。
含 physical_intensity_score 的 0-100 范围校验。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from boss_tool.enums import PhysicalIntensityCategory, ShiftType, WalkingIntensity


class PhysicalIntensityResult(BaseModel):
    """劳动强度判定结果。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    physical_intensity_category: PhysicalIntensityCategory = Field(...)
    physical_intensity_score: int | None = Field(default=None, ge=0, le=100, description="0-100")
    physical_intensity_evidence: str | None = Field(default=None, description="命中的原文片段")

    # ===== 子字段（17 项证据） =====
    sitting_allowed: bool | None = Field(default=None, description="是否允许坐岗")
    prolonged_standing: bool | None = Field(default=None, description="是否需长时间站立")
    patrol_required: bool | None = Field(default=None, description="是否需要巡逻")
    walking_intensity: WalkingIntensity | None = Field(default=None, description="走动强度")
    stair_climbing_required: bool | None = Field(default=None, description="是否需要爬楼")
    lifting_required: bool | None = Field(default=None, description="是否需要搬运")
    lifting_weight_text: str | None = Field(default=None, description="搬运重量原文")
    garbage_transport_required: bool | None = Field(default=None, description="是否涉及垃圾清运")
    outdoor_work: bool | None = Field(default=None, description="是否户外作业")
    high_temperature_exposure: bool | None = Field(default=None, description="是否高温暴晒")
    work_area_text: str | None = Field(default=None, description="工作区域描述原文")
    shift_type: ShiftType | None = Field(default=None, description="班次类型")
    night_shift_required: bool | None = Field(default=None, description="是否需要夜班")
    working_hours_text: str | None = Field(default=None, description="每日工时原文")
    rest_schedule_text: str | None = Field(default=None, description="休息制度原文")

    physical_needs_review: bool = Field(..., description="是否需要人工复核劳动强度")

    @field_validator("physical_intensity_score")
    @classmethod
    def _validate_score_range(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v < 0 or v > 100:
            raise ValueError(f"physical_intensity_score 必须在 0-100 范围内，当前为 {v}")
        return v


__all__ = ["PhysicalIntensityResult"]
