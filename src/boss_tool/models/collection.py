"""CollectionMeta 模型：采集元信息。

按设计稿 v0.3 第 5.5 节定义（含缓存与去重字段）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from boss_tool.enums import SkipReason


class CollectionMeta(BaseModel):
    """采集元信息。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    # ===== 基础采集元 =====
    source_page: str = Field(..., min_length=1, description="数据来源页面")
    collected_at: datetime = Field(..., description="采集时间")
    parse_ok: bool = Field(..., description="解析是否成功")
    missing_fields: list[str] = Field(default_factory=list, description="缺失字段列表")
    error_reason: str | None = Field(default=None, description="异常原因")
    manual_reviewed: bool = Field(default=False, description="是否人工复核")
    manual_review_note: str | None = Field(default=None, description="人工复核备注")

    # ===== 缓存与去重（v0.3 补充） =====
    visited_jobs: bool = Field(default=False, description="是否本次运行已访问")
    last_detail_visit_at: datetime | None = Field(default=None, description="上次访问详情页时间")
    detail_content_hash: str | None = Field(
        default=None, description="详情页核心字段哈希；一致时短期不重复访问"
    )
    skip_reason: SkipReason | None = Field(default=None, description="跳过详情页的原因")
    revisit_allowed_at: datetime | None = Field(default=None, description="最早允许重新访问时间")
    list_stage_passed: bool = Field(default=False, description="是否通过阶段 A 列表页初筛")
    detail_visit_count: int = Field(default=0, ge=0, description="累计详情页访问次数（仅记录）")


__all__ = ["CollectionMeta"]
