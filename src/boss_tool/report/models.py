"""P7 报告数据模型。

定义：
- ReportJob: 报告中的单条岗位（聚合 job_list + job_detail + 规则结果 + 年龄适配）
- ReportSummary: 报告汇总统计
- ReportMetadata: 报告元数据（生成时间、参考地点等）
- ReportSection: 报告分区（强烈推荐/可考虑/待人工确认/不符合）
- ReportSectionType: 分区类型枚举

设计原则：
- 所有字段允许为空，渲染层负责安全降级
- 不包含原始 HTML / Cookie / Token
- 不实现持久化（报告为一次性产物）
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from boss_tool.report.age_fit import CandidateAgeFit


class ReportSectionType(str, Enum):
    """报告分区类型。

    - STRONGLY_RECOMMEND: 强烈推荐（绿色）
    - CONSIDER: 可考虑（蓝色）
    - MANUAL_REVIEW: 待人工确认（黄色）
    - NOT_MATCH: 不符合当前条件（红色）
    """

    STRONGLY_RECOMMEND = "strongly_recommend"
    CONSIDER = "consider"
    MANUAL_REVIEW = "manual_review"
    NOT_MATCH = "not_match"


class ReportJob(BaseModel):
    """报告中的单条岗位。

    聚合 job_list + job_detail + 规则引擎结果 + 60 岁适配判断。
    所有字段允许为空，渲染层负责安全降级展示。

    Attributes:
        job_id: 岗位去重 ID
        title: 岗位名称
        salary: 薪资原文
        company: 公司名称
        location: 地区文本
        experience: 经验要求
        education: 学历要求
        job_url: 岗位链接（已脱敏，渲染前再次 sanitize_url）
        employment_type: 就业类型
        description: 岗位描述（已脱敏）
        company_url: 公司链接（已脱敏）
        company_industry: 公司行业
        company_size: 公司规模
        company_stage: 公司融资阶段
        recruiter_name: 招聘者名称
        recruiter_title: 招聘者职位
        recruiter_active: 招聘者活跃度文本
        benefits: 福利标签列表
        tags: 其他标签列表
        normalized_address: 标准化地址
        longitude: 经度
        latitude: 纬度
        distance_meter: 距离（米）
        within_3km: 是否在 3 公里内
        score: 规则引擎总分
        recommend_level: 推荐等级（A/B/C/D）
        job_category: 岗位分类
        age_requirement_text: 年龄要求原文
        age_status: 年龄状态枚举值
        recruiter_active_level: 招聘者活跃等级枚举值
        matched_rules: 命中的规则 ID 列表
        failed_rules: 未命中的规则 ID 列表
        warnings: 警告列表
        explanations: 固定解释文本列表
        labor_intensity_tags: 命中的劳动强度关键字
        score_breakdown: 各项得分明细
        candidate_age_fit: 60 岁适配状态
        candidate_age_fit_reason: 60 岁适配原因文本
        page_no: 列表页页码
        collected_at: 采集时间
        data_source: 数据来源（detail / list_only）
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    job_id: str = Field(..., min_length=1, description="岗位去重 ID")
    title: str | None = Field(default=None, description="岗位名称")
    salary: str | None = Field(default=None, description="薪资原文")
    company: str | None = Field(default=None, description="公司名称")
    location: str | None = Field(default=None, description="地区文本")
    experience: str | None = Field(default=None, description="经验要求")
    education: str | None = Field(default=None, description="学历要求")
    job_url: str | None = Field(default=None, description="岗位链接（已脱敏）")
    employment_type: str | None = Field(default=None, description="就业类型")
    description: str | None = Field(default=None, description="岗位描述（已脱敏）")
    company_url: str | None = Field(default=None, description="公司链接（已脱敏）")
    company_industry: str | None = Field(default=None, description="公司行业")
    company_size: str | None = Field(default=None, description="公司规模")
    company_stage: str | None = Field(default=None, description="公司融资阶段")
    recruiter_name: str | None = Field(default=None, description="招聘者名称")
    recruiter_title: str | None = Field(default=None, description="招聘者职位")
    recruiter_active: str | None = Field(default=None, description="招聘者活跃度文本")
    benefits: list[str] = Field(default_factory=list, description="福利标签")
    tags: list[str] = Field(default_factory=list, description="其他标签")
    normalized_address: str | None = Field(default=None, description="标准化地址")
    longitude: float | None = Field(default=None, description="经度")
    latitude: float | None = Field(default=None, description="纬度")
    distance_meter: float | None = Field(default=None, ge=0.0, description="距离（米）")
    within_3km: bool | None = Field(default=None, description="是否在 3 公里内")
    score: int | None = Field(default=None, ge=0, le=100, description="规则引擎总分")
    recommend_level: str | None = Field(default=None, description="推荐等级 A/B/C/D")
    job_category: str | None = Field(default=None, description="岗位分类")
    age_requirement_text: str | None = Field(default=None, description="年龄要求原文")
    age_status: str | None = Field(default=None, description="年龄状态枚举值")
    recruiter_active_level: str | None = Field(default=None, description="招聘者活跃等级枚举值")
    matched_rules: list[str] = Field(default_factory=list, description="命中的规则 ID")
    failed_rules: list[str] = Field(default_factory=list, description="未命中的规则 ID")
    warnings: list[str] = Field(default_factory=list, description="警告列表")
    explanations: list[str] = Field(default_factory=list, description="固定解释文本")
    labor_intensity_tags: list[str] = Field(
        default_factory=list, description="命中的劳动强度关键字"
    )
    score_breakdown: dict[str, int] = Field(default_factory=dict, description="各项得分明细")
    candidate_age_fit: CandidateAgeFit = Field(
        default=CandidateAgeFit.UNKNOWN, description="60 岁适配状态"
    )
    candidate_age_fit_reason: str = Field(
        default="未提取到年龄要求，建议联系招聘者确认",
        description="60 岁适配原因文本",
    )
    page_no: int | None = Field(default=None, ge=1, description="列表页页码")
    collected_at: datetime | None = Field(default=None, description="采集时间")
    data_source: str = Field(default="list_only", description="数据来源 detail/list_only")


class ReportSummary(BaseModel):
    """报告汇总统计。

    Attributes:
        total: 岗位总数
        strongly_recommend_count: 强烈推荐数量
        consider_count: 可考虑数量
        manual_review_count: 待人工确认数量
        not_match_count: 不符合数量
        eligible_count: 60 岁适合数量
        review_count: 60 岁需确认数量
        ineligible_count: 60 岁不适合数量
        unknown_count: 60 岁未知数量
        within_3km_count: 3 公里内数量
        detail_source_count: 有详情页数据的数量
        list_only_source_count: 仅列表页数据的数量
    """

    model_config = ConfigDict(extra="forbid")

    total: int = Field(default=0, ge=0, description="岗位总数")
    strongly_recommend_count: int = Field(default=0, ge=0, description="强烈推荐数量")
    consider_count: int = Field(default=0, ge=0, description="可考虑数量")
    manual_review_count: int = Field(default=0, ge=0, description="待人工确认数量")
    not_match_count: int = Field(default=0, ge=0, description="不符合数量")
    eligible_count: int = Field(default=0, ge=0, description="60 岁适合数量")
    review_count: int = Field(default=0, ge=0, description="60 岁需确认数量")
    ineligible_count: int = Field(default=0, ge=0, description="60 岁不适合数量")
    unknown_count: int = Field(default=0, ge=0, description="60 岁未知数量")
    within_3km_count: int = Field(default=0, ge=0, description="3 公里内数量")
    detail_source_count: int = Field(default=0, ge=0, description="有详情页数据的数量")
    list_only_source_count: int = Field(default=0, ge=0, description="仅列表页数据的数量")


class ReportMetadata(BaseModel):
    """报告元数据。

    Attributes:
        generated_at: 报告生成时间
        db_filename: 数据库文件名（不显示完整用户目录）
        rule_version: 规则版本
        reference_location: 参考地点名称
        distance_threshold_m: 距离阈值（米）
        candidate_age: 候选人年龄
        safety_statement: 安全声明
    """

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime = Field(default_factory=datetime.now, description="报告生成时间")
    db_filename: str = Field(default="", description="数据库文件名")
    rule_version: str = Field(default="P6 v1", description="规则版本")
    reference_location: str = Field(default="", description="参考地点名称")
    distance_threshold_m: float = Field(default=3000.0, description="距离阈值（米）")
    candidate_age: int = Field(default=60, description="候选人年龄")
    safety_statement: str = Field(default="", description="安全声明")


class ReportSection(BaseModel):
    """报告分区。

    Attributes:
        section_type: 分区类型
        title: 分区标题（中文）
        color: 分区颜色（CSS 十六进制）
        jobs: 该分区下的岗位列表（已排序）
        count: 该分区岗位数量
    """

    model_config = ConfigDict(extra="forbid")

    section_type: ReportSectionType = Field(..., description="分区类型")
    title: str = Field(..., description="分区标题")
    color: str = Field(..., description="分区颜色")
    jobs: list[ReportJob] = Field(default_factory=list, description="该分区下的岗位列表")
    count: int = Field(default=0, ge=0, description="该分区岗位数量")

    def model_post_init(self, __context: Any) -> None:
        """初始化后同步 count 字段。"""
        # use_enum_values=True 时 section_type 已是字符串
        object.__setattr__(self, "count", len(self.jobs))


__all__ = [
    "ReportJob",
    "ReportSummary",
    "ReportMetadata",
    "ReportSection",
    "ReportSectionType",
]
