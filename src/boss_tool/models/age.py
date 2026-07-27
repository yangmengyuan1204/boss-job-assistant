"""AgeResult 模型：年龄目标判定结果。

按设计稿 v0.3 第 5.2 节与第六节定义。
含一致性校验：保证 age_target_category 与 age_match_category / accepts_candidate_age /
is_exact_65_cap / age_needs_review 之间不自相矛盾。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from boss_tool.enums import (
    AGE_TARGET_TO_MATCH,
    AgeMatchCategory,
    AgeTargetCategory,
    BoundaryRisk,
    Confidence,
)


class AgeResult(BaseModel):
    """年龄目标判定结果。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    candidate_age: int = Field(
        ..., ge=0, le=150, description="求职者当前年龄，固定 60（写入 config）"
    )
    age_evidence_raw: str | None = Field(default=None, description="年龄要求原文证据")
    age_min: int | None = Field(default=None, ge=0, le=150, description="岗位最低年龄")
    age_max: int | None = Field(default=None, ge=0, le=150, description="岗位最高年龄")

    # ===== v0.3 新增：6 级分类与最高优先标记 =====
    is_exact_65_cap: bool = Field(default=False, description="是否为明确 65 岁以下目标岗位")
    age_target_category: AgeTargetCategory = Field(..., description="6 级 age_target_category")
    age_match_category: AgeMatchCategory = Field(..., description="高层分类（由映射得出）")

    accepts_candidate_age: bool | None = Field(
        default=None, description="是否接受已满 60 岁求职者；manual_review/unknown 时为 null"
    )
    age_match_reason: str | None = Field(default=None, description="判定理由")
    age_rule_id: str | None = Field(default=None, description="命中规则 ID")
    boundary_risk: BoundaryRisk = Field(..., description="边界风险等级")
    age_confidence: Confidence = Field(..., description="判定置信度")
    age_needs_review: bool = Field(..., description="是否需要人工复核")

    @model_validator(mode="after")
    def _validate_consistency(self) -> AgeResult:
        """一致性校验。

        校验规则：
        1. age_target_category 必须映射出正确的 age_match_category。
        2. rejects_60 必须 accepts_candidate_age=False 且 age_needs_review=False。
        3. boundary_60 必须 age_needs_review=True 且 accepts_candidate_age=None。
        4. exact_65_cap 必须 is_exact_65_cap=True 且 age_match_category=eligible。
        5. 非 exact_65_cap 不得 is_exact_65_cap=True。
        6. manual_review / unknown 状态 accepts_candidate_age 必须为 None。
        7. eligible 状态 accepts_candidate_age 必须为 True。
        8. ineligible 状态 accepts_candidate_age 必须为 False。
        9. age_min <= age_max（若两者都存在）。
        """
        cat = self.age_target_category
        # use_enum_values=True 时 cat 已是字符串
        cat_str = cat.value if isinstance(cat, AgeTargetCategory) else cat

        # 1. age_target_category 与 age_match_category 必须一致
        expected_match = AGE_TARGET_TO_MATCH[AgeTargetCategory(cat_str)]
        match_str = (
            self.age_match_category.value
            if isinstance(self.age_match_category, AgeMatchCategory)
            else self.age_match_category
        )
        if match_str != expected_match.value:
            raise ValueError(
                f"age_target_category={cat_str} 应映射为 age_match_category={expected_match.value}"
                f"，但实际为 {match_str}"
            )

        # 2. exact_65_cap 必须 is_exact_65_cap=True
        if cat_str == AgeTargetCategory.EXACT_65_CAP.value and not self.is_exact_65_cap:
            raise ValueError("exact_65_cap 必须 is_exact_65_cap=True")

        # 3. 非 exact_65_cap 不得 is_exact_65_cap=True
        if cat_str != AgeTargetCategory.EXACT_65_CAP.value and self.is_exact_65_cap:
            raise ValueError(f"age_target_category={cat_str} 不得 is_exact_65_cap=True")

        # 4. rejects_60 校验
        if cat_str == AgeTargetCategory.REJECTS_60.value:
            if self.accepts_candidate_age is not False:
                raise ValueError("rejects_60 必须 accepts_candidate_age=False")
            if self.age_needs_review:
                raise ValueError("rejects_60 必须 age_needs_review=False")

        # 5. boundary_60 校验
        if cat_str == AgeTargetCategory.BOUNDARY_60.value:
            if not self.age_needs_review:
                raise ValueError("boundary_60 必须 age_needs_review=True")
            if self.accepts_candidate_age is not None:
                raise ValueError("boundary_60 必须 accepts_candidate_age=None")
            if self.boundary_risk != BoundaryRisk.HIGH.value and not (
                isinstance(self.boundary_risk, BoundaryRisk)
                and self.boundary_risk == BoundaryRisk.HIGH
            ):
                raise ValueError("boundary_60 必须 boundary_risk=high")

        # 6. no_explicit_age 校验
        if cat_str == AgeTargetCategory.NO_EXPLICIT_AGE.value:
            if not self.age_needs_review:
                raise ValueError("no_explicit_age 必须 age_needs_review=True")
            if self.accepts_candidate_age is not None:
                raise ValueError("no_explicit_age 必须 accepts_candidate_age=None")

        # 7. eligible 类（exact_65_cap / range_includes_60_to_65 / alternative_accepts_60）校验
        if cat_str in (
            AgeTargetCategory.EXACT_65_CAP.value,
            AgeTargetCategory.RANGE_INCLUDES_60_TO_65.value,
            AgeTargetCategory.ALTERNATIVE_ACCEPTS_60.value,
        ):
            if self.accepts_candidate_age is not True:
                raise ValueError(f"{cat_str} 必须 accepts_candidate_age=True")
            if self.age_needs_review:
                raise ValueError(f"{cat_str} 必须 age_needs_review=False")

        # 8. age_min <= age_max
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError(f"age_min ({self.age_min}) 不能大于 age_max ({self.age_max})")

        return self


__all__ = ["AgeResult"]
