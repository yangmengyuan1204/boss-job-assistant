"""P2 侦察阶段专用数据模型。

本模块定义 P2 阶段页面侦察与本地解析所需的全部数据结构：

- PageType / PageTypeDetection: 公开页面类型识别（基于 DOM 与 URL，不读 Cookie）
- ObservedJobCard: 列表页单个岗位卡片（字段缺失允许为 None）
- ObservedJobDetail: 详情页结构化字段
- ParseDiagnostics: 解析诊断（命中数/缺失/歧义/警告）
- FixtureMeta: 本地 fixture 元数据（不含敏感信息）

设计原则：
- 所有字段缺失允许为 None，不得伪造 "未知" 字符串填充 None
- 列表字段使用 default_factory=list，禁止可变默认值
- 不复用最终 JobRecord，避免伪造必填字段
- 不做业务判断（年龄/劳动强度/距离/评分），仅做 trim 与空白规范化
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class PageType(str, Enum):
    """公开页面类型（基于 DOM 结构与 URL 路径识别）。"""

    HOME = "home"
    LOGIN = "login"
    VERIFICATION = "verification"
    SEARCH_LIST = "search_list"
    JOB_DETAIL = "job_detail"
    EMPTY_RESULTS = "empty_results"
    ERROR = "error"
    UNKNOWN = "unknown"


class PageTypeDetection(BaseModel):
    """页面类型识别结果。

    confidence 在 [0.0, 1.0]：
    - 1.0: 唯一明确命中
    - 0.5: 多类型命中，取最低
    - 0.0: 无任何证据

    不得把"没有登录按钮"简单等价为"已登录"。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=False)

    page_type: PageType = Field(default=PageType.UNKNOWN, description="识别到的页面类型")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度")
    evidence: list[str] = Field(default_factory=list, description="命中证据（描述性）")
    warnings: list[str] = Field(default_factory=list, description="警告（如多类型冲突）")


class ObservedJobCard(BaseModel):
    """列表页单个岗位卡片（侦察阶段）。

    所有字段缺失允许为 None。空列表使用 default_factory=list。
    不做业务判断，仅 trim 与空白规范化。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=False)

    source_index: int = Field(description="卡片在列表中的原始序号（0-based）")
    job_name: str | None = Field(default=None, description="岗位名称")
    job_url: str | None = Field(
        default=None, description="岗位链接（已标准化为绝对 URL，去 query/fragment）"
    )
    salary_text: str | None = Field(default=None, description="薪资原文（不做数值解析）")
    area_text: str | None = Field(default=None, description="地区文本")
    experience_text: str | None = Field(default=None, description="经验要求")
    education_text: str | None = Field(default=None, description="学历要求")
    company_name: str | None = Field(default=None, description="公司名称")
    company_url: str | None = Field(default=None, description="公司链接（已标准化）")
    company_industry: str | None = Field(default=None, description="公司行业")
    company_size: str | None = Field(default=None, description="公司规模")
    recruiter_name: str | None = Field(
        default=None, description="招聘者名称（已脱敏？由 fixture 决定）"
    )
    recruiter_title: str | None = Field(default=None, description="招聘者职位")
    recruiter_active_text: str | None = Field(default=None, description="招聘者活跃度文本")
    benefits: list[str] = Field(default_factory=list, description="岗位福利标签")
    tags: list[str] = Field(default_factory=list, description="其他标签")
    warnings: list[str] = Field(default_factory=list, description="该卡片的解析警告")


class ObservedJobDetail(BaseModel):
    """详情页结构化字段（侦察阶段）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=False)

    job_name: str | None = Field(default=None, description="岗位名称")
    salary_text: str | None = Field(default=None, description="薪资原文")
    location_text: str | None = Field(default=None, description="位置文本")
    experience_text: str | None = Field(default=None, description="经验要求")
    education_text: str | None = Field(default=None, description="学历要求")
    description: str | None = Field(
        default=None, description="岗位描述（保留换行语义，去除多余空行）"
    )
    address_text: str | None = Field(default=None, description="详细地址")
    company_name: str | None = Field(default=None, description="公司名称")
    company_industry: str | None = Field(default=None, description="公司行业")
    company_size: str | None = Field(default=None, description="公司规模")
    recruiter_name: str | None = Field(default=None, description="招聘者名称")
    recruiter_title: str | None = Field(default=None, description="招聘者职位")
    recruiter_active_text: str | None = Field(default=None, description="招聘者活跃度文本")
    publish_or_active_text: str | None = Field(default=None, description="发布或活跃时间文本")
    benefits: list[str] = Field(default_factory=list, description="岗位福利标签")
    tags: list[str] = Field(default_factory=list, description="其他标签")
    warnings: list[str] = Field(default_factory=list, description="解析警告")


class ParseDiagnostics(BaseModel):
    """解析诊断信息。

    解析器不得只返回成功或失败，必须说明：
    - 找到多少岗位卡片
    - 每个关键选择器命中多少
    - 哪些字段缺失
    - 哪些字段出现多个候选（歧义）
    - 页面结构是否可能变化
    - 是否建议人工复查 fixture
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=False)

    page_type: PageType = Field(default=PageType.UNKNOWN, description="识别到的页面类型")
    selector_version: str = Field(description="使用的选择器版本标识")
    root_matches: dict[str, int] = Field(
        default_factory=dict, description="页面级/卡片级根选择器命中数"
    )
    field_matches: dict[str, int] = Field(default_factory=dict, description="字段级选择器命中数")
    missing_required_fields: list[str] = Field(default_factory=list, description="缺失的必填字段名")
    ambiguous_fields: list[str] = Field(
        default_factory=list, description="出现多个候选匹配的字段（歧义）"
    )
    warnings: list[str] = Field(default_factory=list, description="全局警告")
    parser_success: bool = Field(default=False, description="解析器是否成功产出结构化数据")
    card_count: int = Field(default=0, description="解析到的岗位卡片数量")
    suggest_manual_review: bool = Field(default=False, description="是否建议人工复查 fixture")


class FixtureMeta(BaseModel):
    """本地 fixture 元数据（与 .html fixture 并存的 .meta.json）。

    不得保存：
    - 完整原 URL
    - query / fragment
    - 用户身份信息
    - Cookie
    - 浏览器 profile 路径
    - 登录状态详情
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=False)

    fixture_version: int = Field(default=1, description="fixture 格式版本")
    fixture_id: str = Field(default_factory=lambda: uuid4().hex, description="fixture 唯一 ID")
    captured_at: datetime = Field(default_factory=datetime.now, description="采集时间 ISO-8601")
    page_type: PageType = Field(default=PageType.UNKNOWN, description="页面类型")
    source_host: str = Field(description="来源 host（仅 host，不含 userinfo/port/query/fragment）")
    source_path: str = Field(description="来源 path（仅 path，去 query/fragment）")
    sanitized: bool = Field(default=True, description="是否经过脱敏处理")
    selector_version: str = Field(default="p2-v1", description="使用的选择器版本")
    notes: list[str] = Field(default_factory=list, description="备注")
    content_sha256: str = Field(description="fixture HTML 内容的 SHA256（用于完整性校验）")


__all__ = [
    "PageType",
    "PageTypeDetection",
    "ObservedJobCard",
    "ObservedJobDetail",
    "ParseDiagnostics",
    "FixtureMeta",
]
