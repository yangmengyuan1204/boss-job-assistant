"""RecruiterInfo 模型：招聘者公开信息。

按设计稿 v0.3 第 5.4 节定义。
仅采集公开可见信息。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from boss_tool.enums import ActivityCategory


class RecruiterInfo(BaseModel):
    """招聘者公开信息（仅公开可见）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    recruiter_name: str | None = Field(default=None, description="招聘者公开显示姓名")
    recruiter_title: str | None = Field(default=None, description="招聘者职位或身份")
    activity_raw: str | None = Field(default=None, description="活跃状态原文")
    activity_category: ActivityCategory = Field(
        default=ActivityCategory.UNKNOWN, description="招聘者活跃分类"
    )
    active_within_3d: bool | None = Field(
        default=None, description="是否最近 3 日内活跃；unknown 时为 null"
    )


__all__ = ["RecruiterInfo"]
