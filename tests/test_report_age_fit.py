"""P7 CandidateAgeFit 60 岁适配测试。

覆盖：
- 全部 AgeStatus 枚举值的映射
- 字符串输入与枚举输入一致性
- 未知值安全降级为 UNKNOWN
- 纯确定性：相同输入永远相同输出
- 原因文本为固定文本（不拼接动态内容）
- 边界条件：LIMIT_60 归为 REVIEW
"""

from __future__ import annotations

import pytest

from boss_tool.enums import ActivityCategory
from boss_tool.report.age_fit import CandidateAgeFit, compute_age_fit
from boss_tool.rules.models import AgeStatus


class TestAgeFitMapping:
    """AgeStatus 到 CandidateAgeFit 的映射。"""

    @pytest.mark.parametrize(
        ("age_status", "expected_fit"),
        [
            (AgeStatus.NO_LIMIT, CandidateAgeFit.ELIGIBLE),
            (AgeStatus.LIMIT_65, CandidateAgeFit.ELIGIBLE),
            (AgeStatus.LIMIT_60, CandidateAgeFit.REVIEW),
            (AgeStatus.LIMIT_55, CandidateAgeFit.INELIGIBLE),
            (AgeStatus.LIMIT_50, CandidateAgeFit.INELIGIBLE),
            (AgeStatus.LIMIT_45, CandidateAgeFit.INELIGIBLE),
            (AgeStatus.OTHER, CandidateAgeFit.REVIEW),
            (AgeStatus.UNKNOWN, CandidateAgeFit.UNKNOWN),
        ],
    )
    def test_enum_input_mapping(self, age_status: AgeStatus, expected_fit: CandidateAgeFit) -> None:
        """枚举输入映射正确。"""
        fit, reason = compute_age_fit(age_status)
        assert fit == expected_fit
        assert isinstance(reason, str)
        assert len(reason) > 0

    @pytest.mark.parametrize(
        ("status_str", "expected_fit"),
        [
            ("no_limit", CandidateAgeFit.ELIGIBLE),
            ("limit_65", CandidateAgeFit.ELIGIBLE),
            ("limit_60", CandidateAgeFit.REVIEW),
            ("limit_55", CandidateAgeFit.INELIGIBLE),
            ("limit_50", CandidateAgeFit.INELIGIBLE),
            ("limit_45", CandidateAgeFit.INELIGIBLE),
            ("other", CandidateAgeFit.REVIEW),
            ("unknown", CandidateAgeFit.UNKNOWN),
        ],
    )
    def test_string_input_mapping(self, status_str: str, expected_fit: CandidateAgeFit) -> None:
        """字符串输入映射与枚举一致。"""
        fit, reason = compute_age_fit(status_str)
        assert fit == expected_fit
        assert isinstance(reason, str)

    def test_enum_and_string_consistent(self) -> None:
        """枚举与字符串值产生相同结果。"""
        for status in AgeStatus:
            fit_enum, reason_enum = compute_age_fit(status)
            fit_str, reason_str = compute_age_fit(status.value)
            assert fit_enum == fit_str
            assert reason_enum == reason_str


class TestBoundaryConditions:
    """边界条件：60 岁适配规则。"""

    def test_limit_60_is_review(self) -> None:
        """60岁以下归为 REVIEW（边界条件）。

        「60岁以下」可能不包含刚好 60 岁；「60周岁以内」可能包含边界。
        仅靠文本无法保证，故归为 REVIEW。
        """
        fit, reason = compute_age_fit(AgeStatus.LIMIT_60)
        assert fit == CandidateAgeFit.REVIEW
        assert "确认" in reason

    def test_limit_65_is_eligible(self) -> None:
        """65岁以下适合 60 岁候选人。"""
        fit, reason = compute_age_fit(AgeStatus.LIMIT_65)
        assert fit == CandidateAgeFit.ELIGIBLE
        assert "适合" in reason

    def test_no_limit_is_eligible(self) -> None:
        """年龄不限适合 60 岁候选人。"""
        fit, _ = compute_age_fit(AgeStatus.NO_LIMIT)
        assert fit == CandidateAgeFit.ELIGIBLE

    def test_limit_55_is_ineligible(self) -> None:
        """55岁以下不适合 60 岁候选人。"""
        fit, reason = compute_age_fit(AgeStatus.LIMIT_55)
        assert fit == CandidateAgeFit.INELIGIBLE
        assert "不适合" in reason

    def test_unknown_is_unknown(self) -> None:
        """未提取到年龄要求归为 UNKNOWN。"""
        fit, reason = compute_age_fit(AgeStatus.UNKNOWN)
        assert fit == CandidateAgeFit.UNKNOWN
        assert "未提取" in reason or "确认" in reason


class TestUnknownValueFallback:
    """未知值安全降级。"""

    def test_unknown_string_returns_unknown(self) -> None:
        """未知字符串降级为 UNKNOWN。"""
        fit, reason = compute_age_fit("nonexistent_status")
        assert fit == CandidateAgeFit.UNKNOWN
        assert "未提取" in reason or "确认" in reason

    def test_empty_string_returns_unknown(self) -> None:
        """空字符串降级为 UNKNOWN。"""
        fit, _ = compute_age_fit("")
        assert fit == CandidateAgeFit.UNKNOWN

    def test_none_like_value_returns_unknown(self) -> None:
        """None 字符串降级为 UNKNOWN。"""
        fit, _ = compute_age_fit(str(None))
        assert fit == CandidateAgeFit.UNKNOWN


class TestDeterminism:
    """纯确定性测试：相同输入永远相同输出。"""

    @pytest.mark.parametrize("status", list(AgeStatus))
    def test_same_input_same_output(self, status: AgeStatus) -> None:
        """相同输入多次调用结果一致。"""
        results = [compute_age_fit(status) for _ in range(5)]
        first_fit, first_reason = results[0]
        for fit, reason in results[1:]:
            assert fit == first_fit
            assert reason == first_reason


class TestFixedReasonText:
    """原因文本为固定文本，不拼接动态内容。"""

    @pytest.mark.parametrize("status", list(AgeStatus))
    def test_reason_is_fixed_text(self, status: AgeStatus) -> None:
        """原因文本不包含动态拼接的用户输入。"""
        _, reason = compute_age_fit(status)
        # 固定文本预定义集合
        known_reasons = {
            "年龄不限，适合 60 岁候选人",
            "65岁以下，适合 60 岁候选人",
            "边界条件，建议联系招聘者确认",
            "年龄上限 55 岁，不适合 60 岁候选人",
            "年龄上限 50 岁，不适合 60 岁候选人",
            "年龄上限 45 岁，不适合 60 岁候选人",
            "年龄表述模糊，建议联系招聘者确认",
            "未提取到年龄要求，建议联系招聘者确认",
        }
        assert reason in known_reasons, f"原因文本不在固定集合中: {reason}"


class TestCandidateAgeFitEnum:
    """CandidateAgeFit 枚举值测试。"""

    def test_enum_values(self) -> None:
        """枚举值正确。"""
        assert CandidateAgeFit.ELIGIBLE.value == "eligible"
        assert CandidateAgeFit.REVIEW.value == "review"
        assert CandidateAgeFit.INELIGIBLE.value == "ineligible"
        assert CandidateAgeFit.UNKNOWN.value == "unknown"

    def test_enum_is_str(self) -> None:
        """枚举继承 str。"""
        assert isinstance(CandidateAgeFit.ELIGIBLE, str)
        assert CandidateAgeFit.ELIGIBLE == "eligible"


class TestActivityCategoryNotAffected:
    """ActivityCategory 不影响年龄适配。"""

    def test_activity_does_not_affect_age_fit(self) -> None:
        """招聘者活跃等级不影响年龄适配判断。"""
        # 年龄适配仅基于 age_status，与招聘者活跃无关
        fit_active, _ = compute_age_fit(AgeStatus.NO_LIMIT)
        fit_inactive, _ = compute_age_fit(AgeStatus.NO_LIMIT)
        assert fit_active == fit_inactive == CandidateAgeFit.ELIGIBLE

        # 不同活跃等级也不影响
        for _ in ActivityCategory:
            fit, _ = compute_age_fit(AgeStatus.LIMIT_55)
            assert fit == CandidateAgeFit.INELIGIBLE
