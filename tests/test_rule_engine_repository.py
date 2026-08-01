"""P6 RuleEngineRepository 测试。

覆盖：
- save_rule_result：UPDATE 已存在 job_detail 记录
- save_rule_result：job_id 不存在返回 False
- get_rule_result：读取持久化的 RuleResult
- get_rule_result：job_id 不存在返回 None
- get_rule_result：规则字段为空返回 None
- count_by_level：按推荐等级统计
- 往返一致性：save 后 get 字段一致
- 规则字段不影响 JobDetailRepository UPSERT 三态判断
- V5 迁移：job_detail 包含规则引擎列与索引

全部使用本地 SQLite，不联网。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from boss_tool.enums import ActivityCategory
from boss_tool.models.job_detail import DetailUpsertOutcome, JobDetailRecord
from boss_tool.rules.engine import JobInput, RuleEngine
from boss_tool.rules.models import AgeStatus, RecommendLevel, RuleResult
from boss_tool.storage.database import Database
from boss_tool.storage.repositories import JobDetailRepository, RuleEngineRepository


# ==================== 辅助函数 ====================
def _make_detail_record(job_id: str = "rule-test-1") -> JobDetailRecord:
    """构造一条 JobDetailRecord 用于测试。"""
    return JobDetailRecord(
        job_id=job_id,
        job_url="https://www.zhipin.com/job_detail/rule-test-1.html",
        title="小区保洁员",
        salary="4000-5000元/月",
        location="杭州·拱墅区",
        description="45岁以下，身体健康",
        company="示例物业",
        recruiter_active="今日活跃",
        tags=["保洁"],
        benefits=["五险一金"],
        collected_at=datetime(2026, 8, 1, 10, 0, 0),
    )


def _make_rule_result() -> RuleResult:
    """构造一条 RuleResult 用于测试。"""
    return RuleResult(
        score=90,
        recommend_level=RecommendLevel.A,
        job_category="保洁",
        age_requirement_text="45岁以下",
        age_status=AgeStatus.LIMIT_45,
        recruiter_active_level=ActivityCategory.ACTIVE_3D,
        distance_meter=2000.0,
        matched_rules=[
            "category:保洁",
            "age:limit_45",
            "recruiter:active_3d",
            "salary:10",
            "distance:30",
        ],
        failed_rules=["labor_intensity:detected"],
        warnings=[],
        explanations=[
            "岗位分类为保洁，加分+30",
            "年龄要求原文：45岁以下（状态：limit_45）",
            "招聘者3日内活跃，加分+20",
            "薪资达4000+，加分+10",
            "距离在3公里内，加分+30",
        ],
        labor_intensity_tags=[],
        score_breakdown={"category": 30, "recruiter": 20, "salary": 10, "distance": 30},
    )


@pytest.fixture
def db(tmp_db_path: Path) -> Database:
    """初始化数据库并返回。"""
    d = Database(tmp_db_path)
    d.initialize()
    return d


@pytest.fixture
def db_with_detail(db: Database) -> Database:
    """初始化数据库并写入一条 job_detail 记录。"""
    repo = JobDetailRepository(db.connection)
    repo.save_job_detail(_make_detail_record())
    db.commit()
    return db


# ==================== V5 迁移验证 ====================
class TestV5Migration:
    """V5 迁移：job_detail 新增规则引擎列与索引。"""

    def test_job_detail_has_rule_columns(self, db: Database) -> None:
        """job_detail 表包含所有规则引擎字段。"""
        cols = {
            row["name"] for row in db.connection.execute("PRAGMA table_info(job_detail)").fetchall()
        }
        expected = {
            "score",
            "recommend_level",
            "job_category",
            "age_requirement_text",
            "age_status",
            "recruiter_active_level",
            "matched_rules_json",
            "failed_rules_json",
            "warnings_json",
            "explanations_json",
            "labor_intensity_tags_json",
            "score_breakdown_json",
        }
        assert expected.issubset(cols), f"缺失列: {expected - cols}"

    def test_rule_indices_exist(self, db: Database) -> None:
        """规则引擎索引存在。"""
        indices = {
            row["name"]
            for row in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='job_detail'"
            ).fetchall()
        }
        assert "idx_job_detail_rule_level" in indices
        assert "idx_job_detail_rule_category" in indices
        assert "idx_job_detail_rule_score" in indices

    def test_schema_version_is_5(self, db: Database) -> None:
        """迁移后 schema_version == 5。"""
        assert db.get_schema_version() == 5

    def test_v5_migration_idempotent(self, tmp_db_path: Path) -> None:
        """V5 迁移幂等：重复 initialize 不报错。"""
        db = Database(tmp_db_path)
        db.initialize()
        db.initialize()  # 重复初始化
        assert db.get_schema_version() == 5
        db.close()


# ==================== save_rule_result ====================
class TestSaveRuleResult:
    """save_rule_result 持久化测试。"""

    def test_save_to_existing_record(self, db_with_detail: Database) -> None:
        """UPDATE 已存在的 job_detail 记录，返回 True。"""
        repo = RuleEngineRepository(db_with_detail.connection)
        result = _make_rule_result()
        ok = repo.save_rule_result("rule-test-1", result)
        assert ok is True
        db_with_detail.commit()

    def test_save_to_missing_job_id_returns_false(self, db: Database) -> None:
        """job_id 不存在时返回 False，不自动创建。"""
        repo = RuleEngineRepository(db.connection)
        result = _make_rule_result()
        ok = repo.save_rule_result("nonexistent", result)
        assert ok is False

    def test_save_overwrites_previous_rule_result(self, db_with_detail: Database) -> None:
        """重复 save 覆盖之前的规则结果。"""
        repo = RuleEngineRepository(db_with_detail.connection)
        first = _make_rule_result()
        repo.save_rule_result("rule-test-1", first)
        db_with_detail.commit()

        # 第二次写入不同结果
        second = RuleResult(
            score=50,
            recommend_level=RecommendLevel.C,
            job_category="保安",
            age_requirement_text=None,
            age_status=AgeStatus.UNKNOWN,
            recruiter_active_level=ActivityCategory.UNKNOWN,
            distance_meter=None,
            matched_rules=["category:保安"],
            failed_rules=["age:extracted", "recruiter:scored", "salary:scored", "distance:scored"],
            warnings=[],
            explanations=["岗位分类为保安，加分+25"],
            labor_intensity_tags=[],
            score_breakdown={"category": 25, "recruiter": 0, "salary": 0, "distance": 0},
        )
        repo.save_rule_result("rule-test-1", second)
        db_with_detail.commit()

        got = repo.get_rule_result("rule-test-1")
        assert got is not None
        assert got.score == 50
        assert got.recommend_level == "C"
        assert got.job_category == "保安"


# ==================== get_rule_result ====================
class TestGetRuleResult:
    """get_rule_result 读取测试。"""

    def test_get_returns_none_when_job_id_missing(self, db: Database) -> None:
        """job_id 不存在时返回 None。"""
        repo = RuleEngineRepository(db.connection)
        assert repo.get_rule_result("nonexistent") is None

    def test_get_returns_none_when_rule_fields_empty(self, db_with_detail: Database) -> None:
        """job_detail 存在但规则字段为空时返回 None。"""
        repo = RuleEngineRepository(db_with_detail.connection)
        # 未 save_rule_result，规则字段均为 NULL
        assert repo.get_rule_result("rule-test-1") is None

    def test_get_returns_result_after_save(self, db_with_detail: Database) -> None:
        """save 后 get 返回一致的 RuleResult。"""
        repo = RuleEngineRepository(db_with_detail.connection)
        original = _make_rule_result()
        repo.save_rule_result("rule-test-1", original)
        db_with_detail.commit()

        got = repo.get_rule_result("rule-test-1")
        assert got is not None
        assert got.score == original.score
        assert got.recommend_level == original.recommend_level
        assert got.job_category == original.job_category
        assert got.age_requirement_text == original.age_requirement_text
        assert got.age_status == original.age_status
        assert got.recruiter_active_level == original.recruiter_active_level
        assert got.matched_rules == original.matched_rules
        assert got.failed_rules == original.failed_rules
        assert got.warnings == original.warnings
        assert got.explanations == original.explanations
        assert got.labor_intensity_tags == original.labor_intensity_tags
        assert got.score_breakdown == original.score_breakdown

    def test_get_with_labor_intensity_warnings(self, db_with_detail: Database) -> None:
        """含劳动强度 warning 的结果可正确往返。"""
        repo = RuleEngineRepository(db_with_detail.connection)
        result = RuleResult(
            score=55,
            recommend_level=RecommendLevel.C,
            job_category="保安",
            age_status=AgeStatus.LIMIT_50,
            recruiter_active_level=ActivityCategory.ACTIVE_THIS_WEEK,
            matched_rules=["category:保安", "labor_intensity:detected"],
            failed_rules=["age:limit_50", "recruiter:active_this_week"],
            warnings=["检测到劳动强度关键字：夜班、搬运"],
            explanations=["岗位分类为保安，加分+25", "检测到劳动强度关键字：夜班、搬运"],
            labor_intensity_tags=["夜班", "搬运"],
            score_breakdown={"category": 25, "recruiter": 10, "salary": 0, "distance": 0},
        )
        repo.save_rule_result("rule-test-1", result)
        db_with_detail.commit()

        got = repo.get_rule_result("rule-test-1")
        assert got is not None
        assert got.labor_intensity_tags == ["夜班", "搬运"]
        assert "夜班" in got.warnings[0]
        assert "搬运" in got.warnings[0]


# ==================== count_by_level ====================
class TestCountByLevel:
    """count_by_level 统计测试。"""

    def test_count_zero_when_no_rule_results(self, db_with_detail: Database) -> None:
        """无规则结果时统计为 0。"""
        repo = RuleEngineRepository(db_with_detail.connection)
        assert repo.count_by_level("A") == 0
        assert repo.count_by_level("B") == 0

    def test_count_after_save(self, db_with_detail: Database) -> None:
        """save 后按等级统计正确。"""
        repo = RuleEngineRepository(db_with_detail.connection)
        repo.save_rule_result("rule-test-1", _make_rule_result())  # level A
        db_with_detail.commit()

        assert repo.count_by_level("A") == 1
        assert repo.count_by_level("B") == 0


# ==================== 不破坏 P4 UPSERT 三态 ====================
class TestRuleFieldsDoNotAffectUpsert:
    """规则字段不影响 JobDetailRepository 的 UPSERT 三态判断。"""

    def test_unchanged_when_only_rule_fields_differ(self, db_with_detail: Database) -> None:
        """仅规则字段变化时，JobDetailRepository 仍判定 UNCHANGED。

        规则字段不在 BUSINESS_FIELDS 中，因此即使规则字段已写入，
        重新 save_job_detail 相同的业务字段仍返回 UNCHANGED。
        """
        # 先写入规则结果
        rule_repo = RuleEngineRepository(db_with_detail.connection)
        rule_repo.save_rule_result("rule-test-1", _make_rule_result())
        db_with_detail.commit()

        # 重新 save_job_detail 相同业务字段
        detail_repo = JobDetailRepository(db_with_detail.connection)
        same_detail = _make_detail_record()
        outcome = detail_repo.save_job_detail(same_detail)
        assert outcome == DetailUpsertOutcome.UNCHANGED

    def test_updated_when_business_field_changes(self, db_with_detail: Database) -> None:
        """业务字段变化时仍判定 UPDATED（规则字段不影响判断）。"""
        rule_repo = RuleEngineRepository(db_with_detail.connection)
        rule_repo.save_rule_result("rule-test-1", _make_rule_result())
        db_with_detail.commit()

        detail_repo = JobDetailRepository(db_with_detail.connection)
        changed_detail = _make_detail_record()
        changed_detail = changed_detail.model_copy(update={"title": "高级保洁员"})
        outcome = detail_repo.save_job_detail(changed_detail)
        assert outcome == DetailUpsertOutcome.UPDATED


# ==================== 端到端：RuleEngine -> Repository ====================
class TestEndToEnd:
    """RuleEngine.evaluate -> RuleEngineRepository 端到端测试。"""

    def test_evaluate_and_persist(self, db_with_detail: Database) -> None:
        """从 JobDetailRecord 构造 JobInput，evaluate 后持久化。"""
        detail_repo = JobDetailRepository(db_with_detail.connection)
        row = detail_repo.get_by_job_id("rule-test-1")
        assert row is not None

        # 从 job_detail 行构造 JobInput
        job_input = JobInput(
            job_id=row["job_id"],
            title=row["title"],
            description=row["description"],
            tags=(),  # job_detail tags_json 暂不解析，简化测试
            salary_text=row["salary"],
            recruiter_active_text=row["recruiter_active"],
            distance_meter=row["distance_meter"] if "distance_meter" in row else None,
        )

        engine = RuleEngine()
        result = engine.evaluate(job_input)

        # 保洁 + 4000 + 今日活跃 = 30 + 10 + 20 = 60（无距离）
        assert result.job_category == "保洁"
        assert result.score == 60
        assert result.recommend_level == RecommendLevel.C

        # 持久化
        rule_repo = RuleEngineRepository(db_with_detail.connection)
        ok = rule_repo.save_rule_result("rule-test-1", result)
        assert ok is True
        db_with_detail.commit()

        # 读回验证
        got = rule_repo.get_rule_result("rule-test-1")
        assert got is not None
        assert got.score == 60
        assert got.job_category == "保洁"

    def test_diagnostics_after_persist(self, db_with_detail: Database) -> None:
        """持久化后生成 Diagnostics 快照。"""
        engine = RuleEngine()
        job_input = JobInput(
            job_id="rule-test-1",
            title="保洁",
            description="45岁以下",
            salary_text="4000",
            recruiter_active_text="今日活跃",
            distance_meter=2000.0,
        )
        result = engine.evaluate(job_input)
        diag = result.to_diagnostics()

        assert diag.score == result.score
        assert diag.recommend_level == result.recommend_level
        assert diag.job_category == "保洁"
        assert diag.age_status == AgeStatus.LIMIT_45
        assert diag.distance_meter == 2000.0
        assert diag.matched_rule_count > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
