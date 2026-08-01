"""P6 规则引擎测试。

覆盖：
- 岗位分类
- 年龄文本提取
- 招聘者活跃解析
- 劳动强度检测
- 薪资解析
- 评分
- 推荐等级
- 边界
- 空字段
- 综合评估

全部 Mock，不联网。
"""

from __future__ import annotations

import pytest

from boss_tool.enums import ActivityCategory
from boss_tool.rules import constants as C
from boss_tool.rules.engine import (
    JobInput,
    RuleEngine,
    classify_category,
    detect_labor_intensity,
    extract_age,
    parse_recruiter_active,
    parse_salary_min,
)
from boss_tool.rules.models import AgeStatus, RecommendLevel, RuleResult
from boss_tool.rules.scoring import (
    recommend_level,
    score_distance,
    score_recruiter_active,
    score_salary,
    total_score,
)


# ==================== 岗位分类 ====================
class TestClassifyCategory:
    """岗位分类测试。"""

    def test_cleaning_by_title(self) -> None:
        assert classify_category("保洁员", None, None) == "保洁"

    def test_cleaning_by_synonym(self) -> None:
        assert classify_category("清洁工", None, None) == "保洁"

    def test_security_by_title(self) -> None:
        assert classify_category("小区保安", None, None) == "保安"

    def test_security_by_synonym_anbao(self) -> None:
        assert classify_category("安保人员", None, None) == "保安"

    def test_gatekeeper_by_title(self) -> None:
        assert classify_category("门卫", None, None) == "门卫"

    def test_dorm_manager_by_title(self) -> None:
        assert classify_category("宿管阿姨", None, None) == "宿管"

    def test_greening_by_title(self) -> None:
        assert classify_category("绿化养护", None, None) == "绿化"

    def test_sanitation_by_title(self) -> None:
        assert classify_category("环卫工", None, None) == "环卫"

    def test_other_when_no_match(self) -> None:
        assert classify_category("销售经理", None, None) == "其他"

    def test_match_in_tags(self) -> None:
        assert classify_category("工作人员", ["保洁"], None) == "保洁"

    def test_match_in_description(self) -> None:
        assert classify_category("工作人员", None, "负责小区保洁工作") == "保洁"

    def test_priority_cleaning_over_security(self) -> None:
        """保洁优先级高于保安（同时命中取首个）。"""
        assert classify_category("保洁保安", None, None) == "保洁"

    def test_empty_all_returns_other(self) -> None:
        assert classify_category(None, None, None) == "其他"

    def test_empty_tags_list_returns_other(self) -> None:
        assert classify_category(None, [], None) == "其他"


# ==================== 年龄提取 ====================
class TestExtractAge:
    """年龄文本提取测试。"""

    def test_no_limit_keyword(self) -> None:
        text, status = extract_age("年龄不限", None, None)
        assert text == "年龄不限"
        assert status == AgeStatus.NO_LIMIT

    def test_no_age_requirement_keyword(self) -> None:
        text, status = extract_age("无年龄要求", None, None)
        assert status == AgeStatus.NO_LIMIT

    def test_limit_45(self) -> None:
        text, status = extract_age("45岁以下", None, None)
        assert "45" in text
        assert status == AgeStatus.LIMIT_45

    def test_limit_50(self) -> None:
        text, status = extract_age("50岁以下优先", None, None)
        assert "50" in text
        assert status == AgeStatus.LIMIT_50

    def test_limit_55(self) -> None:
        text, status = extract_age("55岁以下", None, None)
        assert status == AgeStatus.LIMIT_55

    def test_limit_60(self) -> None:
        text, status = extract_age("60岁以下", None, None)
        assert status == AgeStatus.LIMIT_60

    def test_limit_65(self) -> None:
        text, status = extract_age("65岁以下", None, None)
        assert status == AgeStatus.LIMIT_65

    def test_zhou_sui_suffix(self) -> None:
        """'周岁以下' 后缀也能匹配。"""
        text, status = extract_age("60周岁以下", None, None)
        assert status == AgeStatus.LIMIT_60

    def test_other_age_range(self) -> None:
        """18-50岁 区间归为 OTHER。"""
        text, status = extract_age("18-50岁", None, None)
        assert status == AgeStatus.OTHER
        assert text is not None

    def test_other_age_not_in_buckets(self) -> None:
        """40岁以下（不在档位内）归为 OTHER。"""
        text, status = extract_age("40岁以下", None, None)
        assert status == AgeStatus.OTHER

    def test_age_in_description(self) -> None:
        text, status = extract_age(None, None, "要求年龄60岁以下，身体健康")
        assert status == AgeStatus.LIMIT_60

    def test_no_age_text_returns_unknown(self) -> None:
        text, status = extract_age("保洁员", None, "负责清洁工作")
        assert text is None
        assert status == AgeStatus.UNKNOWN

    def test_empty_all_returns_unknown(self) -> None:
        text, status = extract_age(None, None, None)
        assert text is None
        assert status == AgeStatus.UNKNOWN


# ==================== 招聘者活跃解析 ====================
class TestParseRecruiterActive:
    """招聘者活跃解析测试。"""

    def test_today_active(self) -> None:
        assert parse_recruiter_active("今日活跃") == ActivityCategory.ACTIVE_3D

    def test_just_now_active(self) -> None:
        assert parse_recruiter_active("刚刚活跃") == ActivityCategory.ACTIVE_3D

    def test_yesterday_active(self) -> None:
        assert parse_recruiter_active("昨日活跃") == ActivityCategory.ACTIVE_3D

    def test_3d_active(self) -> None:
        assert parse_recruiter_active("3日内活跃") == ActivityCategory.ACTIVE_3D

    def test_this_week_active(self) -> None:
        assert parse_recruiter_active("本周活跃") == ActivityCategory.ACTIVE_THIS_WEEK

    def test_7d_active(self) -> None:
        assert parse_recruiter_active("7日内活跃") == ActivityCategory.ACTIVE_THIS_WEEK

    def test_long_ago_text(self) -> None:
        """有文本但不匹配关键词。"""
        assert parse_recruiter_active("本月活跃") == ActivityCategory.ACTIVE_LONG_AGO

    def test_none_returns_unknown(self) -> None:
        assert parse_recruiter_active(None) == ActivityCategory.UNKNOWN

    def test_empty_returns_unknown(self) -> None:
        assert parse_recruiter_active("") == ActivityCategory.UNKNOWN

    def test_whitespace_returns_unknown(self) -> None:
        assert parse_recruiter_active("   ") == ActivityCategory.UNKNOWN


# ==================== 劳动强度检测 ====================
class TestDetectLaborIntensity:
    """劳动强度关键字检测测试。"""

    def test_single_keyword_in_title(self) -> None:
        assert detect_labor_intensity("搬运工", None, None) == ["搬运"]

    def test_multiple_keywords(self) -> None:
        tags = detect_labor_intensity("搬运工", None, "需要夜班，长期站立")
        assert "搬运" in tags
        assert "夜班" in tags
        assert "长期站立" in tags

    def test_keyword_in_description(self) -> None:
        assert detect_labor_intensity("保洁", None, "涉及高空作业") == ["高空"]

    def test_keyword_in_tags(self) -> None:
        assert detect_labor_intensity("员工", ["流水线"], None) == ["流水线"]

    def test_no_keyword_returns_empty(self) -> None:
        assert detect_labor_intensity("保洁员", None, "负责清洁") == []

    def test_dedup_preserves_order(self) -> None:
        """重复关键字去重，保持首次出现顺序。"""
        tags = detect_labor_intensity("搬运", None, "搬运装卸")
        assert tags == ["搬运", "装卸"]

    def test_empty_all_returns_empty(self) -> None:
        assert detect_labor_intensity(None, None, None) == []

    def test_all_keywords_covered(self) -> None:
        """所有定义的关键字都能被检测。"""
        for kw in C.LABOR_INTENSITY_KEYWORDS:
            assert detect_labor_intensity(kw, None, None) == [kw], f"关键字 {kw} 未被检测"


# ==================== 薪资解析 ====================
class TestParseSalaryMin:
    """薪资最低值解析测试。"""

    def test_range_4000_6000(self) -> None:
        assert parse_salary_min("4000-6000") == 4000

    def test_with_unit(self) -> None:
        assert parse_salary_min("4000元/月") == 4000

    def test_k_suffix(self) -> None:
        assert parse_salary_min("4k-6k") == 4000

    def test_mianyi_returns_none(self) -> None:
        assert parse_salary_min("面议") is None

    def test_salary_mianyi_returns_none(self) -> None:
        assert parse_salary_min("薪资面议") is None

    def test_none_returns_none(self) -> None:
        assert parse_salary_min(None) is None

    def test_empty_returns_none(self) -> None:
        assert parse_salary_min("") is None

    def test_unparseable_returns_none(self) -> None:
        assert parse_salary_min("abc") is None

    def test_single_number(self) -> None:
        assert parse_salary_min("5000") == 5000


# ==================== 评分 ====================
class TestScoring:
    """评分函数测试。"""

    def test_score_distance_within_3km(self) -> None:
        assert score_distance(2999.0) == 30

    def test_score_distance_boundary_3000(self) -> None:
        assert score_distance(3000.0) == 30

    def test_score_distance_3_to_5km(self) -> None:
        assert score_distance(3001.0) == 10
        assert score_distance(5000.0) == 10

    def test_score_distance_over_5km(self) -> None:
        assert score_distance(5001.0) == 0

    def test_score_distance_none(self) -> None:
        assert score_distance(None) == 0

    def test_score_recruiter_3d(self) -> None:
        assert score_recruiter_active("active_3d") == 20

    def test_score_recruiter_7d(self) -> None:
        assert score_recruiter_active("active_this_week") == 10

    def test_score_recruiter_other(self) -> None:
        assert score_recruiter_active("active_long_ago") == 0
        assert score_recruiter_active("unknown") == 0

    def test_score_salary_high(self) -> None:
        assert score_salary(4000) == 10

    def test_score_salary_low(self) -> None:
        assert score_salary(3000) == 5
        assert score_salary(3999) == 5

    def test_score_salary_other(self) -> None:
        assert score_salary(2999) == 0
        assert score_salary(None) == 0

    def test_total_score_capped_at_100(self) -> None:
        assert total_score(30, 30, 20, 10, 50) == 100

    def test_total_score_normal(self) -> None:
        assert total_score(30, 20, 10, 10) == 70


# ==================== 推荐等级 ====================
class TestRecommendLevel:
    """推荐等级测试。"""

    def test_level_a_85(self) -> None:
        assert recommend_level(85) == RecommendLevel.A

    def test_level_a_100(self) -> None:
        assert recommend_level(100) == RecommendLevel.A

    def test_level_b_84(self) -> None:
        assert recommend_level(84) == RecommendLevel.B

    def test_level_b_70(self) -> None:
        assert recommend_level(70) == RecommendLevel.B

    def test_level_c_69(self) -> None:
        assert recommend_level(69) == RecommendLevel.C

    def test_level_c_50(self) -> None:
        assert recommend_level(50) == RecommendLevel.C

    def test_level_d_49(self) -> None:
        assert recommend_level(49) == RecommendLevel.D

    def test_level_d_0(self) -> None:
        assert recommend_level(0) == RecommendLevel.D


# ==================== RuleResult 模型 ====================
class TestRuleResult:
    """RuleResult 模型测试。"""

    def test_valid_result(self) -> None:
        r = RuleResult(score=85, recommend_level=RecommendLevel.A, job_category="保洁")
        assert r.score == 85
        assert r.recommend_level == "A"
        assert r.job_category == "保洁"

    def test_score_below_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="score"):
            RuleResult(score=-1, recommend_level=RecommendLevel.D, job_category="其他")

    def test_score_over_100_rejected(self) -> None:
        with pytest.raises(ValueError, match="score"):
            RuleResult(score=101, recommend_level=RecommendLevel.A, job_category="保洁")

    def test_to_db_dict_serializes_lists(self) -> None:
        r = RuleResult(
            score=70,
            recommend_level=RecommendLevel.B,
            job_category="保安",
            matched_rules=["category:保安"],
            warnings=["检测到劳动强度关键字：夜班"],
        )
        d = r.to_db_dict()
        assert d["score"] == 70
        assert d["recommend_level"] == "B"
        assert d["job_category"] == "保安"
        assert "category:保安" in d["matched_rules_json"]
        assert "夜班" in d["warnings_json"]


# ==================== 综合评估 ====================
class TestRuleEngineEvaluate:
    """RuleEngine.evaluate 综合测试。"""

    def setup_method(self) -> None:
        self.engine = RuleEngine()

    def test_full_match_high_score(self) -> None:
        """保洁 + 3km内 + 3日活跃 + 4000薪资 -> 高分。"""
        job = JobInput(
            job_id="j1",
            title="小区保洁员",
            description="45岁以下，身体健康",
            salary_text="4000-5000元/月",
            recruiter_active_text="今日活跃",
            distance_meter=2000.0,
        )
        result = self.engine.evaluate(job)
        assert result.job_category == "保洁"
        assert result.age_status == AgeStatus.LIMIT_45
        assert result.recruiter_active_level == ActivityCategory.ACTIVE_3D
        assert result.distance_meter == 2000.0
        # 30(保洁) + 20(3d) + 10(4000) + 30(3km) = 90
        assert result.score == 90
        assert result.recommend_level == RecommendLevel.A

    def test_labor_intensity_adds_warning_not_delete(self) -> None:
        """劳动强度关键字增加 warning，不删除岗位。"""
        job = JobInput(
            job_id="j2",
            title="搬运工",
            description="需要夜班",
            salary_text="3000元/月",
            recruiter_active_text="本周活跃",
            distance_meter=4000.0,
        )
        result = self.engine.evaluate(job)
        assert "搬运" in result.labor_intensity_tags
        assert "夜班" in result.labor_intensity_tags
        assert len(result.warnings) > 0
        # 仍返回结果，不删除
        assert result.score >= 0

    def test_empty_input_low_score(self) -> None:
        """空字段输入得到低分（D）。"""
        job = JobInput(job_id="j3")
        result = self.engine.evaluate(job)
        assert result.score == 0
        assert result.recommend_level == RecommendLevel.D
        assert result.job_category == "其他"
        assert result.age_status == AgeStatus.UNKNOWN
        assert result.recruiter_active_level == ActivityCategory.UNKNOWN
        assert result.distance_meter is None

    def test_explanations_are_fixed_text(self) -> None:
        """explanations 全部为固定文本（非空字符串）。"""
        job = JobInput(
            job_id="j4",
            title="保安",
            description="50岁以下",
            salary_text="3000",
            recruiter_active_text="刚刚活跃",
            distance_meter=2500.0,
        )
        result = self.engine.evaluate(job)
        assert len(result.explanations) > 0
        for exp in result.explanations:
            assert isinstance(exp, str)
            assert len(exp) > 0

    def test_deterministic_same_input_same_output(self) -> None:
        """相同输入产生相同输出（确定性）。"""
        job = JobInput(
            job_id="j5",
            title="保洁",
            salary_text="4000",
            recruiter_active_text="今日活跃",
            distance_meter=1000.0,
        )
        r1 = self.engine.evaluate(job)
        r2 = self.engine.evaluate(job)
        assert r1.score == r2.score
        assert r1.recommend_level == r2.recommend_level
        assert r1.explanations == r2.explanations

    def test_score_breakdown_keys(self) -> None:
        """score_breakdown 包含 category/recruiter/salary/distance。"""
        job = JobInput(job_id="j6", title="保洁", distance_meter=1000.0)
        result = self.engine.evaluate(job)
        assert "category" in result.score_breakdown
        assert "recruiter" in result.score_breakdown
        assert "salary" in result.score_breakdown
        assert "distance" in result.score_breakdown

    def test_score_capped_at_100(self) -> None:
        """总分上限 100。"""
        job = JobInput(
            job_id="j7",
            title="保洁",  # 30
            salary_text="4000",  # 10
            recruiter_active_text="今日活跃",  # 20
            distance_meter=500.0,  # 30
        )
        result = self.engine.evaluate(job)
        assert result.score <= 100

    def test_matched_and_failed_rules_populated(self) -> None:
        """matched_rules 与 failed_rules 均被填充。"""
        job = JobInput(job_id="j8", title="保洁", distance_meter=1000.0)
        result = self.engine.evaluate(job)
        # 保洁命中 -> matched 含 category
        assert any("category" in r for r in result.matched_rules)
        # 薪资为空 -> failed 含 salary
        assert any("salary" in r for r in result.failed_rules)


# ==================== RuleDiagnostics ====================
class TestRuleDiagnostics:
    """RuleDiagnostics 诊断快照测试。"""

    def setup_method(self) -> None:
        self.engine = RuleEngine()

    def test_diagnostics_fields_populated(self) -> None:
        """to_diagnostics 返回完整诊断字段。"""
        job = JobInput(
            job_id="d1",
            title="保洁",
            description="60岁以下",
            salary_text="4000",
            recruiter_active_text="今日活跃",
            distance_meter=1500.0,
        )
        result = self.engine.evaluate(job)
        diag = result.to_diagnostics()
        assert diag.matched_rule_count == len(result.matched_rules)
        assert diag.failed_rule_count == len(result.failed_rules)
        assert diag.score == result.score
        assert diag.recommend_level == result.recommend_level
        assert diag.age_status == result.age_status
        assert diag.job_category == result.job_category
        assert diag.distance_meter == result.distance_meter

    def test_diagnostics_empty_input(self) -> None:
        """空输入的诊断快照。"""
        job = JobInput(job_id="d2")
        result = self.engine.evaluate(job)
        diag = result.to_diagnostics()
        assert diag.score == 0
        assert diag.recommend_level == RecommendLevel.D
        assert diag.job_category == "其他"
        assert diag.age_status == AgeStatus.UNKNOWN
        assert diag.distance_meter is None
        assert diag.matched_rule_count >= 0
        assert diag.failed_rule_count >= 0

    def test_diagnostics_matched_count_matches(self) -> None:
        """命中规则数量与 matched_rules 长度一致。"""
        job = JobInput(
            job_id="d3",
            title="保洁",
            description="搬运",  # 劳动强度关键字
            salary_text="4000",
            recruiter_active_text="今日活跃",
            distance_meter=1000.0,
        )
        result = self.engine.evaluate(job)
        diag = result.to_diagnostics()
        assert diag.matched_rule_count == len(result.matched_rules)
        assert diag.failed_rule_count == len(result.failed_rules)
        # 命中 + 未命中 = 总规则项（category/age/recruiter/labor/salary/distance = 6）
        assert diag.matched_rule_count + diag.failed_rule_count == 6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
