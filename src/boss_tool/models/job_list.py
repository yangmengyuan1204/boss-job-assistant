"""P3 搜索结果列表采集数据模型。

定义 JobListRecord：列表页采集的单条岗位记录。
从 ObservedJobCard 转换而来，仅包含列表页公开可见字段，
不含详情页、年龄判断、劳动强度、评分等后续阶段字段。

P3.1 新增：
- UpsertOutcome: 三态 UPSERT 结果（NEW / UPDATED / UNCHANGED）
- BulkUpsertResult: 批量 UPSERT 结构化统计
- DiagnosticsSummary: ParseDiagnostics 的安全摘要（仅含计数与字段名，不含页面原文）

去重键 job_id 推导优先级：
1. 从 job_url 路径提取末段（如 /job_detail/abc.html → abc）
2. 若 job_url 为空，使用 SHA256(title|company|location) 前 16 位加 "hash:" 前缀

P3.2 fallback job_id 稳定性修正：
- 移除 salary 字段（易变：工资调整不应改变岗位身份）
- 移除 page_no / collected_at / experience / education（易变采集元数据或次级属性）
- 保留相对稳定的岗位身份字段：title + company + location
- location 变化视为不同岗位身份（在测试中明确）
- 文本规范化：strip + 连续空白折叠 + 空值统一处理
- 相同 title + company + location 必须产生稳定相同的 fallback job_id

URL 安全：
- from_observed_card 在转换边界再次调用 sanitize_url，形成防御性校验
- 不信任调用者一定已经脱敏
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from boss_tool.models.observed_page import ObservedJobCard, ParseDiagnostics
from boss_tool.parsers.sanitization import sanitize_url


def _normalize_identity_text(text: str | None) -> str:
    """规范化岗位身份文本。

    - strip 首尾空白
    - 连续空白折叠为单个空格
    - 空值统一处理为空字符串
    """
    if not text:
        return ""
    # 折叠连续空白（含换行、制表符）为单个空格
    import re

    return re.sub(r"\s+", " ", text.strip())


def derive_job_id(
    job_url: str | None,
    title: str | None,
    company: str | None,
    salary: str | None,  # noqa: ARG001 保留签名兼容，P3.2 不再参与 fallback 哈希
    location: str | None = None,
) -> str:
    """推导岗位去重 job_id。

    优先级：
    1. job_url 路径末段（去 .html 后缀）—— 仍优先，不变
    2. fallback: SHA256(title|company|location) 前 16 位 + "hash:" 前缀

    P3.2 修正：
    - fallback 不再包含 salary（工资变化不应改变岗位身份）
    - fallback 不包含 page_no / collected_at / experience / education
    - 保留相对稳定的身份字段：title + company + location
    - 文本规范化：strip + 连续空白折叠
    - 相同 title + company + location 产生稳定相同的 job_id
    - location 变化视为不同岗位身份

    Args:
        job_url: 已脱敏的岗位 URL（https://host/path，无 query/fragment）
        title: 岗位名称
        company: 公司名
        salary: 薪资原文（P3.2 起不再参与 fallback，保留参数仅为签名兼容）
        location: 地区文本（P3.2 新增，参与 fallback 身份）

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

    # 回退：基于 title + company + location 的哈希（P3.2：移除 salary）
    norm_title = _normalize_identity_text(title)
    norm_company = _normalize_identity_text(company)
    norm_location = _normalize_identity_text(location)
    content = f"{norm_title}|{norm_company}|{norm_location}"
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
    # ===== P5 地理字段（允许为空，不参与 UPSERT 三态判断）=====
    normalized_address: str | None = Field(
        default=None, description="标准化地址（P5 地址标准化后）"
    )
    longitude: float | None = Field(
        default=None, ge=-180.0, le=180.0, description="经度（P5 地理编码）"
    )
    latitude: float | None = Field(
        default=None, ge=-90.0, le=90.0, description="纬度（P5 地理编码）"
    )
    distance_meter: float | None = Field(
        default=None, ge=0.0, description="与中心点直线距离（米，P5 距离计算）"
    )
    within_3km: bool | None = Field(default=None, description="是否在 3 公里内（P5 距离筛选）")

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

        在转换边界再次调用 sanitize_url()，形成防御性校验：
        - 不信任调用者一定已经脱敏
        - job_url / company_url 任一未通过校验则置为 None
        - 不会因未脱敏 URL 直接保存到数据库

        Args:
            card: P2 解析器产出的岗位卡片
            page_no: 采集页码
            collected_at: 采集时间（默认当前时间）

        Returns:
            JobListRecord 实例
        """
        # URL 二次防御：即使上游未脱敏，此处强制再次校验
        # sanitize_url(None) / sanitize_url("") → None，安全
        safe_job_url = sanitize_url(card.job_url) if card.job_url else None
        safe_company_url = sanitize_url(card.company_url) if card.company_url else None

        job_id = derive_job_id(
            job_url=safe_job_url,
            title=card.job_name,
            company=card.company_name,
            salary=card.salary_text,
            location=card.area_text,
        )
        return cls(
            job_id=job_id,
            title=card.job_name,
            salary=card.salary_text,
            company=card.company_name,
            location=card.area_text,
            experience=card.experience_text,
            education=card.education_text,
            job_url=safe_job_url,
            company_url=safe_company_url,
            page_no=page_no,
            collected_at=collected_at or datetime.now(),
        )


class UpsertOutcome(str, Enum):
    """三态 UPSERT 结果。

    - NEW: 数据库中不存在该 job_id，新增插入
    - UPDATED: 数据库中已存在该 job_id，且业务字段发生变化
    - UNCHANGED: 数据库中已存在该 job_id，且业务字段全部相同（仅 collected_at 可能变化）
    """

    NEW = "new"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class BulkUpsertResult(BaseModel):
    """批量 UPSERT 结构化统计。

    new_count + updated_count + unchanged_count == 输入记录数。
    不得通过 len(records) - new - updated 伪造 unchanged。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    new_count: int = Field(default=0, ge=0, description="新增数量")
    updated_count: int = Field(default=0, ge=0, description="更新数量（业务字段变化）")
    unchanged_count: int = Field(default=0, ge=0, description="重复数量（业务字段未变）")

    @property
    def total(self) -> int:
        """返回总处理数，应等于输入记录数。"""
        return self.new_count + self.updated_count + self.unchanged_count


class DiagnosticsSummary(BaseModel):
    """ParseDiagnostics 的安全摘要。

    仅包含计数、字段名、选择器名等安全标识。
    不包含：
    - 完整原始 HTML
    - 原始 DOM 片段
    - 完整匹配文本
    - 页面原文样本

    字段映射基于现有 ParseDiagnostics 能力：
    - card_count: diagnostics.card_count
    - warning_count: len(diagnostics.warnings)
    - missing_required_fields: diagnostics.missing_required_fields（全部卡片均未命中的必填字段）
    - missing_field_counts: 每个字段缺失卡片数 = card_count - field_matches[name]
      （仅当 0 < 缺失数 < card_count 时记录，全缺失由 missing_required_fields 覆盖）
    - selector_miss_count: field_matches 中命中数为 0 的字段数
    - fallback_count: len(diagnostics.ambiguous_fields)
      （现有列表页 diagnostics 不填充 ambiguous_fields，此值当前恒为 0）
    - parser_success: diagnostics.parser_success
    - suggest_manual_review: diagnostics.suggest_manual_review
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    card_count: int = Field(default=0, ge=0, description="解析到的岗位卡片数量")
    warning_count: int = Field(default=0, ge=0, description="诊断警告数量")
    missing_required_fields: list[str] = Field(
        default_factory=list, description="全部卡片均未命中的必填字段名"
    )
    missing_field_counts: dict[str, int] = Field(
        default_factory=dict, description="部分卡片缺失的字段名及缺失数"
    )
    selector_miss_count: int = Field(default=0, ge=0, description="命中数为 0 的字段数")
    fallback_count: int = Field(default=0, ge=0, description="多候选回退数量（当前恒为 0）")
    parser_success: bool = Field(default=False, description="解析是否成功产出结构化数据")
    suggest_manual_review: bool = Field(default=False, description="是否建议人工复查")


def build_diagnostics_summary(diagnostics: ParseDiagnostics) -> DiagnosticsSummary:
    """从 ParseDiagnostics 构建安全摘要。

    只保留字段名、计数、选择器名等安全标识。
    不得保留真实页面文本样本。

    Args:
        diagnostics: P2 解析诊断

    Returns:
        DiagnosticsSummary 安全摘要
    """
    card_count = diagnostics.card_count
    field_matches = diagnostics.field_matches or {}

    # 每个字段的缺失卡片数 = card_count - 命中数
    # 命中数可能 > card_count（多值字段 benefits/tags 可有多条），此时缺失数为 0
    missing_field_counts: dict[str, int] = {}
    selector_miss_count = 0
    for name, hits in field_matches.items():
        if hits == 0:
            selector_miss_count += 1
            continue  # 全缺失的字段由 missing_required_fields 覆盖，不重复计入
        missing = card_count - hits
        if missing > 0 and missing < card_count:
            missing_field_counts[name] = missing

    return DiagnosticsSummary(
        card_count=card_count,
        warning_count=len(diagnostics.warnings or []),
        missing_required_fields=list(diagnostics.missing_required_fields or []),
        missing_field_counts=missing_field_counts,
        selector_miss_count=selector_miss_count,
        fallback_count=len(diagnostics.ambiguous_fields or []),
        parser_success=diagnostics.parser_success,
        suggest_manual_review=diagnostics.suggest_manual_review,
    )


__all__ = [
    "JobListRecord",
    "derive_job_id",
    "UpsertOutcome",
    "BulkUpsertResult",
    "DiagnosticsSummary",
    "build_diagnostics_summary",
]
