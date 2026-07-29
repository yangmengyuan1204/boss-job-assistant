"""schemas 模块测试：严格 JSON Schema 校验。

覆盖规格第十节：
- 非法 JSON 被拒绝
- Schema 多字段或少字段被拒绝
- status / risk_level / decision 枚举校验
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from automation.schemas import (
    AutomationConfig,
    PlannerOutput,
    ReviewerDecision,
    RunState,
    TaskCommand,
)


# ==================== PlannerOutput ====================
def _valid_planner_payload() -> dict:
    return {
        "status": "continue",
        "task_id": "T-001",
        "summary": "演示任务",
        "reason": "验证 dry-run 流程",
        "files_allowed": ["tests/automation/_demo.txt"],
        "steps": ["创建示例文件", "运行 pytest"],
        "commands": [{"argv": ["pytest", "-q"], "timeout_seconds": 300, "purpose": "运行测试"}],
        "acceptance_criteria": ["测试通过"],
        "risk_level": "low",
        "requires_network": False,
        "requires_human": False,
    }


class TestPlannerOutput:
    def test_valid_payload_accepted(self) -> None:
        out = PlannerOutput.model_validate(_valid_planner_payload())
        assert out.status == "continue"
        assert out.task_id == "T-001"
        assert out.commands[0].argv == ["pytest", "-q"]
        assert out.risk_level == "low"

    def test_invalid_json_rejected(self) -> None:
        bad = "{ this is not json "
        with pytest.raises(json.JSONDecodeError):
            json.loads(bad)

    @pytest.mark.parametrize("missing", list(_valid_planner_payload().keys()))
    def test_missing_field_rejected(self, missing: str) -> None:
        payload = _valid_planner_payload()
        payload.pop(missing)
        with pytest.raises(ValidationError):
            PlannerOutput.model_validate(payload)

    def test_extra_field_rejected(self) -> None:
        payload = _valid_planner_payload()
        payload["sneaky"] = "evil"
        with pytest.raises(ValidationError):
            PlannerOutput.model_validate(payload)

    @pytest.mark.parametrize("status", ["CONTINUE", "paused", "", "done!"])
    def test_invalid_status_rejected(self, status: str) -> None:
        payload = _valid_planner_payload()
        payload["status"] = status
        with pytest.raises(ValidationError):
            PlannerOutput.model_validate(payload)

    @pytest.mark.parametrize("risk", ["HIGH", "extreme", "", "low "])
    def test_invalid_risk_level_rejected(self, risk: str) -> None:
        payload = _valid_planner_payload()
        payload["risk_level"] = risk
        with pytest.raises(ValidationError):
            PlannerOutput.model_validate(payload)

    def test_empty_argv_rejected(self) -> None:
        payload = _valid_planner_payload()
        payload["commands"] = [{"argv": [], "timeout_seconds": 10, "purpose": "空命令"}]
        with pytest.raises(ValidationError):
            PlannerOutput.model_validate(payload)

    def test_non_string_argv_rejected(self) -> None:
        payload = _valid_planner_payload()
        payload["commands"] = [{"argv": ["pytest", 123], "timeout_seconds": 10, "purpose": "x"}]
        with pytest.raises(ValidationError):
            PlannerOutput.model_validate(payload)

    def test_high_risk_payload_flagged(self) -> None:
        """risk_level=high 本身可解析，但 policy 层应拒绝；schema 仅负责结构。"""
        payload = _valid_planner_payload()
        payload["risk_level"] = "high"
        out = PlannerOutput.model_validate(payload)
        assert out.risk_level == "high"


# ==================== TaskCommand ====================
class TestTaskCommand:
    def test_valid(self) -> None:
        cmd = TaskCommand(argv=["ruff", "check", "src"], timeout_seconds=120, purpose="lint")
        assert cmd.argv == ["ruff", "check", "src"]
        assert cmd.timeout_seconds == 120

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            TaskCommand(argv=["x"], timeout_seconds=0, purpose="x")

    def test_timeout_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            TaskCommand(argv=["x"], timeout_seconds=3601, purpose="x")


# ==================== ReviewerDecision ====================
def _valid_reviewer_payload() -> dict:
    return {
        "decision": "continue",
        "reason": "测试通过，继续",
        "tests_passed": True,
        "acceptance_met": True,
        "next_focus": "下一轮处理 X",
        "human_action": "",
    }


class TestReviewerDecision:
    def test_valid(self) -> None:
        dec = ReviewerDecision.model_validate(_valid_reviewer_payload())
        assert dec.decision == "continue"

    @pytest.mark.parametrize("missing", list(_valid_reviewer_payload().keys()))
    def test_missing_field_rejected(self, missing: str) -> None:
        payload = _valid_reviewer_payload()
        payload.pop(missing)
        with pytest.raises(ValidationError):
            ReviewerDecision.model_validate(payload)

    def test_extra_field_rejected(self) -> None:
        payload = _valid_reviewer_payload()
        payload["extra"] = 1
        with pytest.raises(ValidationError):
            ReviewerDecision.model_validate(payload)

    @pytest.mark.parametrize("decision", ["CONTINUE", "retry", "", "done "])
    def test_invalid_decision_rejected(self, decision: str) -> None:
        payload = _valid_reviewer_payload()
        payload["decision"] = decision
        with pytest.raises(ValidationError):
            ReviewerDecision.model_validate(payload)


# ==================== AutomationConfig ====================
class TestAutomationConfig:
    def test_defaults_are_safe(self) -> None:
        cfg = AutomationConfig(
            project_goal="demo",
            allowed_paths=["src", "tests", "automation"],
            denied_paths=[".git", ".env"],
            allowed_commands=["python", "pytest"],
        )
        # 首次运行必须默认安全
        assert cfg.dry_run is True
        assert cfg.network_access is False
        assert cfg.live_boss_access is False
        assert cfg.max_iterations == 12
        assert cfg.require_clean_git_start is True

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AutomationConfig(
                project_goal="demo",
                allowed_paths=["src"],
                denied_paths=[".git"],
                allowed_commands=["python"],
                unknown_field=1,  # type: ignore[call-arg]
            )

    def test_max_iterations_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            AutomationConfig(
                project_goal="demo",
                allowed_paths=["src"],
                denied_paths=[".git"],
                allowed_commands=["python"],
                max_iterations=0,
            )


# ==================== RunState ====================
class TestRunState:
    def test_new_state_defaults(self) -> None:
        state = RunState(run_id="run-1", start_time="2026-07-29T00:00:00")
        assert state.current_iteration == 0
        assert state.consecutive_failures == 0
        assert state.completed_tasks == []
        assert state.current_status == "created"

    def test_serializes_to_json(self) -> None:
        state = RunState(run_id="run-1", start_time="2026-07-29T00:00:00")
        data = json.loads(state.model_dump_json())
        assert data["run_id"] == "run-1"
