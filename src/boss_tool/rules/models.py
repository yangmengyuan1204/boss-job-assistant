"""P6 规则引擎数据模型。

定义：
- AgeStatus: 年龄状态枚举（只提取，不推断）
- RecommendLevel: 推荐等级枚举（A/B/C/D）
- RuleResult: 统一规则结果

约束：
- RuleResult.explanations 必须全部为固定文本
- RuleResult.score 在 0..100
- 不接入 LLM / 机器学习
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from boss_tool.enums import ActivityCategory


class AgeStatus(str, Enum):
    """年龄状态枚举（只提取原文，不推断是否可干）。

    - UNKNOWN: 无年龄文本
    - NO_LIMIT: 年龄不限 / 无年龄要求
    - LIMIT_45: 45岁以下
    - LIMIT_50: 50岁以下
    - LIMIT_55: 55岁以下
    - LIMIT_60: 60岁以下
    - LIMIT_65: 65岁以下
    - OTHER: 其他年龄表述（如"18-50岁"、"40岁以下优先"等无法归入上述档位）
    """

    UNKNOWN = "unknown"
    NO_LIMIT = "no_limit"
    LIMIT_45 = "limit_45"
    LIMIT_50 = "limit_50"
    LIMIT_55 = "limit_55"
    LIMIT_60 = "limit_60"
    LIMIT_65 = "limit_65"
    OTHER = "other"


class RecommendLevel(str, Enum):
    """推荐等级。

    - A: >=85
    - B: 70~84
    - C: 50~69
    - D: <50
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class RuleResult(BaseModel):
    """规则引擎统一结果。

    Attributes:
        score: 总分（0..100）
        recommend_level: 推荐等级（A/B/C/D）
        job_category: 岗位分类（保洁/保安/门卫/宿管/绿化/环卫/其他）
        age_requirement_text: 年龄要求原文（None 表示无文本）
        age_status: 年龄状态
        recruiter_active_level: 招聘者活跃等级
        distance_meter: 距离（米），None 表示无数据
        matched_rules: 命中的规则 ID 列表
        failed_rules: 未命中的规则 ID 列表
        warnings: 警告列表（如劳动强度关键字）
        explanations: 固定解释文本列表（每条必须为固定文本）
        labor_intensity_tags: 命中的劳动强度关键字
        score_breakdown: 各项得分明细
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    score: int = Field(..., ge=0, le=100, description="总分 0..100")
    recommend_level: RecommendLevel = Field(..., description="推荐等级 A/B/C/D")
    job_category: str = Field(..., description="岗位分类")
    age_requirement_text: str | None = Field(default=None, description="年龄要求原文")
    age_status: AgeStatus = Field(default=AgeStatus.UNKNOWN, description="年龄状态")
    recruiter_active_level: ActivityCategory = Field(
        default=ActivityCategory.UNKNOWN, description="招聘者活跃等级"
    )
    distance_meter: float | None = Field(default=None, ge=0.0, description="距离（米）")
    matched_rules: list[str] = Field(default_factory=list, description="命中的规则 ID")
    failed_rules: list[str] = Field(default_factory=list, description="未命中的规则 ID")
    warnings: list[str] = Field(default_factory=list, description="警告列表")
    explanations: list[str] = Field(default_factory=list, description="固定解释文本列表")
    labor_intensity_tags: list[str] = Field(
        default_factory=list, description="命中的劳动强度关键字"
    )
    score_breakdown: dict[str, int] = Field(
        default_factory=dict, description="各项得分明细（rule_id -> score）"
    )

    @field_validator("score")
    @classmethod
    def _validate_score(cls, v: int) -> int:
        if v < 0 or v > 100:
            raise ValueError(f"score 必须在 0..100，当前为 {v}")
        return v

    def to_db_dict(self) -> dict[str, Any]:
        """转换为数据库持久化字段字典（供 RuleEngineRepository 使用）。

        列表字段序列化为 JSON 字符串。
        """
        import json

        from boss_tool.rules.constants import MAX_SCORE

        def _enum_str(v: Any) -> str | None:
            if v is None:
                return None
            if hasattr(v, "value"):
                return v.value
            return str(v)

        return {
            "score": min(self.score, MAX_SCORE),
            "recommend_level": _enum_str(self.recommend_level),
            "job_category": self.job_category,
            "age_requirement_text": self.age_requirement_text,
            "age_status": _enum_str(self.age_status),
            "recruiter_active_level": _enum_str(self.recruiter_active_level),
            "matched_rules_json": json.dumps(self.matched_rules, ensure_ascii=False),
            "failed_rules_json": json.dumps(self.failed_rules, ensure_ascii=False),
            "warnings_json": json.dumps(self.warnings, ensure_ascii=False),
            "explanations_json": json.dumps(self.explanations, ensure_ascii=False),
            "labor_intensity_tags_json": json.dumps(self.labor_intensity_tags, ensure_ascii=False),
            "score_breakdown_json": json.dumps(self.score_breakdown, ensure_ascii=False),
        }

    def to_diagnostics(self) -> RuleDiagnostics:
        """生成诊断快照（规则命中数量/评分/等级/年龄/分类/距离）。

        用于日志输出与人工核查，不参与持久化。
        """
        return RuleDiagnostics(
            matched_rule_count=len(self.matched_rules),
            failed_rule_count=len(self.failed_rules),
            score=self.score,
            recommend_level=self.recommend_level,
            age_status=self.age_status,
            job_category=self.job_category,
            distance_meter=self.distance_meter,
        )


class RuleDiagnostics(BaseModel):
    """规则引擎诊断快照。

    输出字段（P6 要求）：
    - matched_rule_count: 命中规则数量
    - failed_rule_count: 未命中规则数量
    - score: 评分
    - recommend_level: 推荐等级
    - age_status: 年龄状态
    - job_category: 岗位分类
    - distance_meter: 距离（米）

    仅用于诊断/日志，不参与持久化。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    matched_rule_count: int = Field(..., ge=0, description="命中规则数量")
    failed_rule_count: int = Field(..., ge=0, description="未命中规则数量")
    score: int = Field(..., ge=0, le=100, description="评分 0..100")
    recommend_level: RecommendLevel = Field(..., description="推荐等级 A/B/C/D")
    age_status: AgeStatus = Field(..., description="年龄状态")
    job_category: str = Field(..., description="岗位分类")
    distance_meter: float | None = Field(default=None, ge=0.0, description="距离（米）")


__all__ = ["AgeStatus", "RecommendLevel", "RuleResult", "RuleDiagnostics"]
