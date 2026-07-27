"""RunRecord 模型：单次运行记录。

按设计稿 v0.3 第 5.6 节与第 21.7 节（账号健康记录补充）定义。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from boss_tool.enums import RunStatus, StopReason


class RunRecord(BaseModel):
    """单次运行记录（run_logs 一行）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    run_id: str = Field(..., min_length=1, description="本次运行唯一 ID")
    started_at: datetime = Field(..., description="开始时间")
    ended_at: datetime | None = Field(default=None, description="结束时间")
    status: RunStatus = Field(..., description="运行状态")
    stop_reason: StopReason | None = Field(default=None, description="停止原因")

    # ===== 账号健康记录（v0.3 补充） =====
    account_warning_detected: bool = Field(default=False, description="是否出现账号警告")
    warning_type: str | None = Field(default=None, description="警告类型")
    warning_text: str | None = Field(default=None, description="警告原文/截图描述")

    # ===== 运行统计 =====
    page_count: int = Field(default=0, ge=0, description="本次累计页面访问数")
    detail_page_count: int = Field(default=0, ge=0, description="本次详情页访问数")
    search_page_count: int = Field(default=0, ge=0, description="本次搜索列表页访问数")
    cache_hit_count: int = Field(default=0, ge=0, description="缓存命中数量")
    duplicate_skip_count: int = Field(default=0, ge=0, description="跳过重复岗位数量")
    list_filter_skip_count: int = Field(default=0, ge=0, description="列表页初筛跳过数量")
    run_duration_seconds: int = Field(default=0, ge=0, description="运行时长（秒）")
    consecutive_errors: int = Field(default=0, ge=0, description="连续错误数")
    stopped_by_safety_rule: bool = Field(default=False, description="是否由安全规则停止")
    user_aborted: bool = Field(default=False, description="是否用户主动停止")
    last_successful_url: str | None = Field(default=None, description="最后成功访问的 URL")


__all__ = ["RunRecord"]
