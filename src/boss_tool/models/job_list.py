"""P3 搜索结果列表采集数据模型。

定义 JobListRecord：列表页采集的单条岗位记录。
从 ObservedJobCard 转换而来，仅包含列表页公开可见字段，
不含详情页、年龄判断、劳动强度、评分等后续阶段字段。

去重键 job_id 推导优先级：
1. 从 job_url 路径提取末段（如 /job_detail/abc.html → abc）
2. 若 job_url 为空，使用 SHA256(title|company|salary) 前 16 位加 "hash:" 前缀
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from boss_tool.models.observed_page import ObservedJobCard


def derive_job_id(
    job_url: str | None,
    title: str | None,
    company: str | None,
    salary: str | None,
) -> str:
    """推导岗位去重 job_id。

    优先级：
    1. job_url 路径末段（去 .html 后缀）
    2. SHA256(title|company|salary) 前 16 位 + "hash:" 前缀

    Args:
        job_url: 已脱敏的岗位 URL（https://host/path，无 query/fragment）
        title: 岗位名称
        company: 公司名
        salary: 薪资原文

    Returns:
        稳定的 job_id 字符串
    """
    if job_url:
        parsed = urlparse(job_url)
        path = parsed.path.rstrip("/")
        last_segment = path.split("/")[-1] if path else ""
        if last_segment:
            # 去除 .html / .htm 后缀
            for suffix in (".html", ".htm"):
                if last_segment.endswith(suffix):
                    last_segment = last_segment[: -len(suffix)]
                    break
            if last_segment:
                return last_segment

    # 回退：基于 title + company + salary 的哈希
    content = f"{title or ''}|{company or ''}|{salary or ''}"
    return "hash:" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


class JobListRecord(BaseModel):
    """搜索结果列表页采集的单条岗位记录。

    从 ObservedJobCard 转换而来，仅包含列表页公开可见字段。
    job_url / company_url 已通过 sanitize_url() 脱敏（P2.1 严格安全版本）。
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    job_id: str = Field(..., min_length=1, description="岗位去重 ID（从 URL 或哈希推导）")
    title: str | None = Field(default=None, description="岗位名称")
    salary: str | None = Field(default=None, description="薪资原文（不做数值解析）")
    company: str | None = Field(default=None, description="公司名称")
    location: str | None = Field(default=None, description="地区文本")
    experience: str | None = Field(default=None, description="经验要求")
    education: str | None = Field(default=None, description="学历要求")
    job_url: str | None = Field(
        default=None, description="岗位链接（已脱敏，无 query/fragment/userinfo/port）"
    )
    company_url: str | None = Field(
        default=None, description="公司链接（已脱敏，无 query/fragment/userinfo/port）"
    )
    page_no: int | None = Field(default=None, ge=1, description="采集时的页码（人工指定）")
    collected_at: datetime = Field(default_factory=datetime.now, description="采集时间 ISO-8601")

    @field_validator("job_id")
    @classmethod
    def _validate_job_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("job_id 不能为空")
        return v.strip()

    @classmethod
    def from_observed_card(
        cls,
        card: ObservedJobCard,
        *,
        page_no: int | None = None,
        collected_at: datetime | None = None,
    ) -> JobListRecord:
        """从 ObservedJobCard 转换为 JobListRecord。

        Args:
            card: P2 解析器产出的岗位卡片
            page_no: 采集页码
            collected_at: 采集时间（默认当前时间）

        Returns:
            JobListRecord 实例
        """
        job_id = derive_job_id(
            job_url=card.job_url,
            title=card.job_name,
            company=card.company_name,
            salary=card.salary_text,
        )
        return cls(
            job_id=job_id,
            title=card.job_name,
            salary=card.salary_text,
            company=card.company_name,
            location=card.area_text,
            experience=card.experience_text,
            education=card.education_text,
            job_url=card.job_url,
            company_url=card.company_url,
            page_no=page_no,
            collected_at=collected_at or datetime.now(),
        )


__all__ = ["JobListRecord", "derive_job_id"]
