"""P4 岗位详情页采集数据模型。

定义 JobDetailRecord：详情页采集的单条岗位记录。
从 ObservedJobDetail 转换而来，包含详情页公开可见字段，
不含年龄判断、劳动强度、评分等后续阶段字段。

P4 三态 UPSERT：
- DetailUpsertOutcome: NEW / UPDATED / UNCHANGED
- 列表/标签类字段采用确定性序列化（去重 + 排序）参与比较
- UNCHANGED 时仍刷新 collected_at
- collected_at 单独变化不算 UPDATED

job_id 关联：
- 优先从安全 job_url 推导（复用 P3 derive_job_id 规则）
- 与 job_list 表的 job_id 同源，不另起一套
- 无有效 job_url 时不得创建详情记录（避免不可靠身份）

URL 安全：
- from_observed_detail 在持久化边界再次调用 sanitize_url
- 不保存原始 HTML / Cookie / Token / 手机号 / 邮箱
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from boss_tool.models.job_list import derive_job_id
from boss_tool.models.observed_page import ObservedJobDetail
from boss_tool.parsers.sanitization import sanitize_text, sanitize_url

# ==================== 描述长度上限 ====================
# job_description 可能很长，超过此长度安全截断并在 Diagnostics 记录固定警告代码
MAX_DESCRIPTION_LENGTH: int = 5000

# 描述超长截断时 Diagnostics 记录的固定警告代码（不含页面原文）
DESCRIPTION_TRUNCATED_CODE: str = "DESCRIPTION_TRUNCATED"


def _normalize_list(values: list[str] | None) -> list[str]:
    """确定性规范化列表字段。

    - None / 空列表统一为空列表
    - 去重（保持首次出现顺序）
    - 不排序（保留页面顺序作为稳定策略，便于人工核对）
    """
    if not values:
        return []
    seen: set[str] = set()
    unique: list[str] = []
    for v in values:
        if v is None:
            continue
        v_norm = v.strip() if isinstance(v, str) else v
        if not v_norm:
            continue
        if v_norm not in seen:
            seen.add(v_norm)
            unique.append(v_norm)
    return unique


def _truncate_description(desc: str | None) -> tuple[str | None, bool]:
    """安全截断描述。

    Returns:
        (截断后的描述, 是否发生截断)
    """
    if desc is None:
        return None, False
    if len(desc) <= MAX_DESCRIPTION_LENGTH:
        return desc, False
    return desc[:MAX_DESCRIPTION_LENGTH], True


class JobDetailRecord(BaseModel):
    """岗位详情页采集的单条记录。

    从 ObservedJobDetail 转换而来，仅包含详情页公开可见字段。
    job_url / company_url 已通过 sanitize_url() 脱敏（P2.1 严格安全版本）。
    description 已通过 sanitize_text 脱敏并限制最大长度。
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )

    job_id: str = Field(
        ..., min_length=1, description="岗位去重 ID（从 URL 或哈希推导，与 job_list 同源）"
    )
    job_url: str | None = Field(
        default=None, description="岗位链接（已脱敏，无 query/fragment/userinfo/port）"
    )
    title: str | None = Field(default=None, description="岗位名称")
    salary: str | None = Field(default=None, description="薪资原文（不做数值解析）")
    location: str | None = Field(default=None, description="地区文本")
    experience: str | None = Field(default=None, description="经验要求")
    education: str | None = Field(default=None, description="学历要求")
    employment_type: str | None = Field(default=None, description="就业类型（如全职/兼职）")
    description: str | None = Field(
        default=None, description="岗位描述（已脱敏、限制最大长度，保留换行语义）"
    )
    company: str | None = Field(default=None, description="公司名称")
    company_url: str | None = Field(
        default=None, description="公司链接（已脱敏，无 query/fragment/userinfo/port）"
    )
    company_industry: str | None = Field(default=None, description="公司行业")
    company_size: str | None = Field(default=None, description="公司规模")
    company_stage: str | None = Field(default=None, description="公司融资阶段")
    recruiter_name: str | None = Field(default=None, description="招聘者名称（已脱敏）")
    recruiter_title: str | None = Field(default=None, description="招聘者职位")
    recruiter_active: str | None = Field(default=None, description="招聘者活跃度文本")
    benefits: list[str] = Field(default_factory=list, description="岗位福利标签（确定性去重）")
    tags: list[str] = Field(default_factory=list, description="其他标签（确定性去重）")
    description_truncated: bool = Field(default=False, description="描述是否因超长被截断")
    collected_at: datetime = Field(default_factory=datetime.now, description="采集时间 ISO-8601")

    @field_validator("job_id")
    @classmethod
    def _validate_job_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("job_id 不能为空")
        return v.strip()

    @classmethod
    def from_observed_detail(
        cls,
        detail: ObservedJobDetail,
        *,
        job_url: str | None = None,
        collected_at: datetime | None = None,
    ) -> JobDetailRecord:
        """从 ObservedJobDetail 转换为 JobDetailRecord。

        在持久化边界再次调用 sanitize_url / sanitize_text，形成防御性校验：
        - 不信任调用者一定已经脱敏
        - job_url / company_url 任一未通过校验则置为 None
        - description 经过 sanitize_text 并限制最大长度
        - benefits / tags 列表确定性去重

        Args:
            detail: P2 详情页解析器产出的 ObservedJobDetail
            job_url: 当前详情页 URL（用于推导 job_id 与持久化）
                     调用方应在进入此函数前先用 sanitize_url 校验
            collected_at: 采集时间（默认当前时间）

        Returns:
            JobDetailRecord 实例

        Raises:
            ValueError: 无有效 job_url 时（无法推导可靠 job_id）
        """
        # URL 二次防御：即使上游未脱敏，此处强制再次校验
        safe_job_url = sanitize_url(job_url) if job_url else None

        # 详情页必须能从安全 job_url 推导 job_id，否则拒绝创建
        # （避免不可靠身份混入数据库）
        if not safe_job_url:
            raise ValueError("无有效 job_url，无法创建可靠的详情记录")

        # 复用 P3 derive_job_id 推导规则（与 job_list 同源）
        job_id = derive_job_id(
            job_url=safe_job_url,
            title=detail.job_name,
            company=detail.company_name,
            salary=detail.salary_text,
            location=detail.location_text,
        )

        # 描述脱敏 + 截断
        sanitized_desc = sanitize_text(detail.description)
        truncated_desc, was_truncated = _truncate_description(sanitized_desc)

        # 文本字段脱敏
        def _safe_text(v: str | None) -> str | None:
            return sanitize_text(v) if v else None

        return cls(
            job_id=job_id,
            job_url=safe_job_url,
            title=_safe_text(detail.job_name),
            salary=_safe_text(detail.salary_text),
            location=_safe_text(detail.location_text),
            experience=_safe_text(detail.experience_text),
            education=_safe_text(detail.education_text),
            employment_type=None,  # ObservedJobDetail 当前无此字段，留空
            description=truncated_desc,
            company=_safe_text(detail.company_name),
            company_url=None,  # ObservedJobDetail 当前无 company_url 字段
            company_industry=_safe_text(detail.company_industry),
            company_size=_safe_text(detail.company_size),
            company_stage=None,  # ObservedJobDetail 当前无 company_stage 字段
            recruiter_name=_safe_text(detail.recruiter_name),
            recruiter_title=_safe_text(detail.recruiter_title),
            recruiter_active=_safe_text(detail.recruiter_active_text),
            benefits=_normalize_list(detail.benefits),
            tags=_normalize_list(detail.tags),
            description_truncated=was_truncated,
            collected_at=collected_at or datetime.now(),
        )


class DetailUpsertOutcome(str, Enum):
    """详情页三态 UPSERT 结果。

    - NEW: 数据库中不存在该 job_id
    - UPDATED: 已存在且业务字段发生变化
    - UNCHANGED: 已存在且业务字段全部相同（仅 collected_at 可能变化）
    """

    NEW = "new"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


__all__ = [
    "JobDetailRecord",
    "DetailUpsertOutcome",
    "MAX_DESCRIPTION_LENGTH",
    "DESCRIPTION_TRUNCATED_CODE",
]
